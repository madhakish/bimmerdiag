"""
ediabas.py - Python wrapper for the EDIABAS API (api64.dll)

Provides a clean interface to BMW's EDIABAS diagnostic system via the
K+DCAN cable. Wraps the native C API with proper type handling for all
EDIABAS result formats (REAL, INTEGER, WORD, LONG, DWORD, TEXT, BINARY).

Requirements:
    - EDIABAS V7.3.0 installed at C:\\EDIABAS
    - 64-bit Python 3.x
    - K+DCAN cable connected and configured (COM port in obd.ini)
    - C:\\Windows\\OBD.INI must exist (see docs/SETUP.md)
    - INPA must NOT be running (exclusive COM port access)
"""

import ctypes
import sys
from pathlib import Path

# EDIABAS API states
APIBUSY = 0
APIREADY = 1
APIBREAK = 2
APIERROR = 3

# EDIABAS result format enum
APIFORMAT_CHAR = 0
APIFORMAT_BYTE = 1
APIFORMAT_INTEGER = 2
APIFORMAT_WORD = 3
APIFORMAT_LONG = 4
APIFORMAT_DWORD = 5
APIFORMAT_TEXT = 6
APIFORMAT_BINARY = 7
APIFORMAT_REAL = 8

# Default DLL path
DEFAULT_API_DLL = r"C:\EDIABAS\Bin\api64.dll"


class EdiabasError(Exception):
    """EDIABAS API error with error code and message."""
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"EDIABAS error {code}: {message}")


class Ediabas:
    """Wrapper for the EDIABAS API."""

    def __init__(self, dll_path=DEFAULT_API_DLL):
        self.dll_path = dll_path
        self.api = None
        self.handle = ctypes.c_uint(0)
        self._connected = False

    def connect(self):
        """Initialize the EDIABAS API connection."""
        if self._connected:
            return

        if not Path(self.dll_path).exists():
            raise FileNotFoundError(f"EDIABAS API DLL not found: {self.dll_path}")

        self.api = ctypes.WinDLL(self.dll_path)
        self._setup_prototypes()

        result = self.api.__apiInit(ctypes.byref(self.handle))
        if not result:
            raise EdiabasError(-1, "apiInit failed - check EDIABAS installation")

        self._connected = True

    def disconnect(self):
        """Close the EDIABAS API connection."""
        if self._connected and self.api:
            self.api.__apiEnd(self.handle)
            self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    def _setup_prototypes(self):
        """Set up ctypes function prototypes for all EDIABAS API functions."""
        a = self.api

        a.__apiInit.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        a.__apiInit.restype = ctypes.c_int

        a.__apiEnd.argtypes = [ctypes.c_uint]
        a.__apiEnd.restype = None

        a.__apiJob.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_char_p,
                               ctypes.c_char_p, ctypes.c_char_p]
        a.__apiJob.restype = None

        a.__apiState.argtypes = [ctypes.c_uint]
        a.__apiState.restype = ctypes.c_int

        a.__apiStateExt.argtypes = [ctypes.c_uint, ctypes.c_int]
        a.__apiStateExt.restype = ctypes.c_int

        a.__apiResultSets.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ushort)]
        a.__apiResultSets.restype = ctypes.c_int

        a.__apiResultNumber.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ushort),
                                        ctypes.c_ushort]
        a.__apiResultNumber.restype = ctypes.c_int

        a.__apiResultName.argtypes = [ctypes.c_uint, ctypes.c_char_p,
                                      ctypes.c_ushort, ctypes.c_ushort]
        a.__apiResultName.restype = ctypes.c_int

        a.__apiResultText.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_char_p,
                                      ctypes.c_ushort, ctypes.c_char_p]
        a.__apiResultText.restype = ctypes.c_int

        a.__apiResultReal.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_double),
                                      ctypes.c_char_p, ctypes.c_ushort]
        a.__apiResultReal.restype = ctypes.c_int

        a.__apiResultInt.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_short),
                                     ctypes.c_char_p, ctypes.c_ushort]
        a.__apiResultInt.restype = ctypes.c_int

        a.__apiResultWord.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ushort),
                                      ctypes.c_char_p, ctypes.c_ushort]
        a.__apiResultWord.restype = ctypes.c_int

        a.__apiResultLong.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_long),
                                      ctypes.c_char_p, ctypes.c_ushort]
        a.__apiResultLong.restype = ctypes.c_int

        a.__apiResultDWord.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong),
                                       ctypes.c_char_p, ctypes.c_ushort]
        a.__apiResultDWord.restype = ctypes.c_int

        a.__apiResultFormat.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int),
                                        ctypes.c_char_p, ctypes.c_ushort]
        a.__apiResultFormat.restype = ctypes.c_int

        a.__apiErrorCode.argtypes = [ctypes.c_uint]
        a.__apiErrorCode.restype = ctypes.c_int

        a.__apiErrorText.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_int]
        a.__apiErrorText.restype = None

    def get_error(self):
        """Get the current error code and text."""
        code = self.api.__apiErrorCode(self.handle)
        buf = ctypes.create_string_buffer(256)
        self.api.__apiErrorText(self.handle, buf, 256)
        return code, buf.value.decode('latin-1', errors='replace')

    def run_job(self, sgbd, job, params="", result_filter="", timeout_ms=10000):
        """Run an EDIABAS job and return structured results.

        Returns a list of dicts, one per result set (set 0 = system, 1+ = data).
        Each dict maps result_name -> value (auto-typed).
        Raises EdiabasError on failure.
        """
        self.api.__apiJob(self.handle, sgbd.encode(), job.encode(),
                          params.encode(), result_filter.encode())

        max_polls = timeout_ms // 100
        state = APIBUSY
        for _ in range(max_polls):
            state = self.api.__apiStateExt(self.handle, 100)
            if state != APIBUSY:
                break

        if state == APIERROR:
            code, text = self.get_error()
            raise EdiabasError(code, text)

        if state != APIREADY:
            raise EdiabasError(-2, f"Job timed out (state={state})")

        return self._read_results()

    def _read_results(self):
        """Read all result sets from the last completed job."""
        sets = ctypes.c_ushort(0)
        self.api.__apiResultSets(self.handle, ctypes.byref(sets))

        result_sets = []
        for s in range(sets.value + 1):
            num = ctypes.c_ushort(0)
            self.api.__apiResultNumber(self.handle, ctypes.byref(num), s)

            result = {}
            for i in range(num.value):
                name_buf = ctypes.create_string_buffer(64)
                if not self.api.__apiResultName(self.handle, name_buf, i, s):
                    continue

                name = name_buf.value.decode('latin-1', errors='replace')
                if name.startswith("_TEL_"):
                    continue

                value = self._read_result_value(name_buf.value, s)
                if value is not None:
                    result[name] = value

            result_sets.append(result)

        return result_sets

    def _read_result_value(self, name_bytes, result_set):
        """Read a single result value, auto-detecting the format."""
        fmt = ctypes.c_int(-1)
        self.api.__apiResultFormat(self.handle, ctypes.byref(fmt), name_bytes, result_set)

        if fmt.value == APIFORMAT_REAL:
            v = ctypes.c_double(0)
            self.api.__apiResultReal(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_INTEGER:
            v = ctypes.c_short(0)
            self.api.__apiResultInt(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_WORD:
            v = ctypes.c_ushort(0)
            self.api.__apiResultWord(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_LONG:
            v = ctypes.c_long(0)
            self.api.__apiResultLong(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_DWORD:
            v = ctypes.c_ulong(0)
            self.api.__apiResultDWord(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_TEXT:
            buf = ctypes.create_string_buffer(1024)
            self.api.__apiResultText(self.handle, buf, name_bytes, result_set, b"")
            return buf.value.decode('latin-1', errors='replace')

        elif fmt.value == APIFORMAT_BINARY:
            return None  # skip binary for now

        else:
            # Fallback: try text
            buf = ctypes.create_string_buffer(1024)
            if self.api.__apiResultText(self.handle, buf, name_bytes, result_set, b""):
                return buf.value.decode('latin-1', errors='replace')
            return None

    def read_value(self, sgbd, job, result_name, result_set=1, params=""):
        """Run a job and read a single named result value.

        Convenience method for reading sensor values.
        Returns the value or None if not found.
        """
        self.api.__apiJob(self.handle, sgbd.encode(), job.encode(),
                          params.encode(), b"")
        for _ in range(100):
            if self.api.__apiStateExt(self.handle, 100) != APIBUSY:
                break

        # Try REAL first (most sensor values)
        v = ctypes.c_double(0)
        if self.api.__apiResultReal(self.handle, ctypes.byref(v),
                                    result_name.encode(), result_set):
            return v.value

        # Try WORD
        wv = ctypes.c_ushort(0)
        if self.api.__apiResultWord(self.handle, ctypes.byref(wv),
                                    result_name.encode(), result_set):
            return float(wv.value)

        # Try LONG
        lv = ctypes.c_long(0)
        if self.api.__apiResultLong(self.handle, ctypes.byref(lv),
                                    result_name.encode(), result_set):
            return float(lv.value)

        # Try TEXT
        tb = ctypes.create_string_buffer(256)
        if self.api.__apiResultText(self.handle, tb, result_name.encode(),
                                    result_set, b""):
            text = tb.value.decode('latin-1', errors='replace')
            try:
                return float(text)
            except ValueError:
                return text

        return None

    def list_jobs(self, sgbd):
        """List all available jobs for a given SGBD."""
        results = self.run_job(sgbd, "_JOBS")
        jobs = []
        for r in results[1:]:  # skip set 0 (system)
            if "JOBNAME" in r:
                jobs.append(r["JOBNAME"])
        return sorted(jobs)

    def identify(self, sgbd):
        """Run IDENT job and return identification data."""
        results = self.run_job(sgbd, "IDENT")
        return results[1] if len(results) > 1 else {}

    def read_faults(self, sgbd):
        """Read fault codes (DTC). Returns list of fault dicts."""
        results = self.run_job(sgbd, "FS_LESEN")
        faults = []
        for r in results[1:]:  # skip set 0
            if "F_ORT_NR" in r:
                faults.append(r)
            elif "JOB_STATUS" in r and r["JOB_STATUS"] == "OKAY":
                continue  # final status set
        return faults
