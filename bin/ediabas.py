"""
ediabas.py - Python wrapper for the EDIABAS API (api64.dll)

Provides a clean interface to BMW's EDIABAS diagnostic system via the
K+DCAN cable. Wraps the native C API with proper type handling for all
EDIABAS result formats (REAL, INTEGER, WORD, LONG, DWORD, TEXT, BINARY).

On Linux, uses pythonnet + Mono to load ediabaslib (uholeschak/ediabaslib)
as an in-process replacement for api64.dll.

Requirements:
    - EDIABAS V7.3.0 installed at C:\\EDIABAS
    - 64-bit Python 3.x
    - K+DCAN cable connected and configured (COM port in obd.ini)
    - C:\\Windows\\OBD.INI must exist (see docs/SETUP.md)
    - INPA must NOT be running (exclusive COM port access)

  Linux:
    - mono-complete, pip install pythonnet
    - ediabaslib built for net481, EdiabasLib.dll in same directory
"""

import ctypes
import sys
from pathlib import Path

_LINUX = sys.platform == "linux"

if _LINUX:
    import clr  # pythonnet

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

_SCRIPT_DIR = Path(__file__).resolve().parent


class EdiabasError(Exception):
    """EDIABAS API error with error code and message."""
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"EDIABAS error {code}: {message}")


if _LINUX:

    class _MonoBackend:
        """Loads EdiabasLib.dll via pythonnet + Mono — in-process, no subprocess."""

        _rt = None

        def __init__(self, lib_path=None):
            self._lib_path = Path(lib_path or (_SCRIPT_DIR / "EdiabasLib.dll"))
            self._ediabas = None

        def connect(self, port=None, ecu_path=None):
            if self._ediabas is not None:
                return

            if not self._lib_path.exists():
                raise FileNotFoundError(
                    f"EdiabasLib.dll not found: {self._lib_path}\n"
                    f"Build ediabaslib for net481: dotnet build -f net481 -c Release\n"
                    f"Then copy EdiabasLib.dll to {_SCRIPT_DIR}/"
                )

            clr.AddReference(str(self._lib_path))
            from EdiabasLib import EdiabasNet, EdInterfaceObd
            _MonoBackend._rt = EdiabasNet.ResultType

            port = port or "/dev/ttyUSB0"
            overrides = f"Interface=STD:OBD;ObdComPort={port};RetryComm=1"
            if ecu_path:
                overrides += f";EcuPath={ecu_path}"

            self._ediabas = EdiabasNet(overrides)
            self._interface = EdInterfaceObd()
            self._interface.IfhName = "STD:OBD"
            self._interface.ComPort = port
            self._ediabas.EdInterfaceClass = self._interface

            if ecu_path:
                self._ediabas.SetConfigProperty("EcuPath", ecu_path)

            self._last_sgbd = None

        def disconnect(self):
            if self._ediabas is not None:
                try:
                    self._ediabas.Dispose()
                except Exception:
                    pass
                self._ediabas = None

        def _run(self, sgbd, job, args="", result_filter=""):
            if self._ediabas is None:
                raise EdiabasError(-1, "Not connected. Call connect() first.")

            if self._last_sgbd != sgbd:
                self._ediabas.ResolveSgbdFile(sgbd)
                self._last_sgbd = sgbd

            self._ediabas.ArgString = args
            self._ediabas.ResultsRequests = result_filter

            try:
                self._ediabas.ExecuteJob(job)
            except Exception as e:
                raise EdiabasError(-1, str(e)) from e

            return self._convert()

        def _convert(self):
            net_sets = self._ediabas.ResultSets
            if net_sets is None:
                return []

            rt = _MonoBackend._rt
            py_sets = []
            for result_set in net_sets:
                item_dict = {}
                for name in result_set.Keys:
                    if name.startswith("_TEL_"):
                        continue
                    rd = result_set[name]
                    rtype = rd.ResType
                    opdata = rd.OpData

                    if rtype in (rt.TypeS, rt.TypeC):
                        value = str(opdata) if opdata is not None else ""
                    elif rtype == rt.TypeR:
                        value = float(opdata) if opdata is not None else 0.0
                    elif rtype == rt.TypeY:
                        value = str(opdata) if opdata is not None else ""
                    else:
                        value = int(opdata) if opdata is not None else 0

                    item_dict[name] = value
                py_sets.append(item_dict)
            return py_sets

        def run_job(self, sgbd, job, params="", result_filter="", timeout_ms=10000):
            return self._run(sgbd, job, params, result_filter)

        def read_value(self, sgbd, job, result_name, result_set=1, params=""):
            sets = self._run(sgbd, job, params)
            if len(sets) > result_set:
                return sets[result_set].get(result_name)
            return None

        def read_results(self, sgbd, job, result_names, params="", result_set=1,
                         timeout_ms=10000):
            sets = self._run(sgbd, job, params)
            if len(sets) > result_set:
                return {name: sets[result_set].get(name) for name in result_names}
            return {name: None for name in result_names}

        def list_jobs(self, sgbd):
            sets = self._run(sgbd, "_JOBS")
            jobs = []
            for s in sets[1:]:
                if "JOBNAME" in s:
                    jobs.append(s["JOBNAME"])
            return sorted(jobs)

        def identify(self, sgbd):
            sets = self._run(sgbd, "IDENT")
            return sets[1] if len(sets) > 1 else {}

        def read_faults(self, sgbd):
            sets = self._run(sgbd, "FS_LESEN")
            faults = []
            for r in sets[1:]:
                if "F_ORT_NR" in r:
                    faults.append(r)
                elif "JOB_STATUS" in r and r["JOB_STATUS"] == "OKAY":
                    continue
            return faults


class Ediabas:
    """Wrapper for the EDIABAS API."""

    def __init__(self, dll_path=DEFAULT_API_DLL):
        if _LINUX:
            self._backend = _MonoBackend()
            self._connected = False
            return
        self.dll_path = dll_path
        self.api = None
        self.handle = ctypes.c_uint(0)
        self._connected = False

    def connect(self, port=None, ecu_path=None):
        """Initialize the EDIABAS API connection."""
        if self._connected:
            return
        if _LINUX:
            self._backend.connect(port=port, ecu_path=ecu_path)
            self._connected = True
            return

        if not Path(self.dll_path).exists():
            raise FileNotFoundError(f"EDIABAS API DLL not found: {self.dll_path}")

        self.api = ctypes.WinDLL(self.dll_path)
        self._setup_prototypes()

        result = self._apiInit(ctypes.byref(self.handle))
        if not result:
            raise EdiabasError(-1, "apiInit failed - check EDIABAS installation")

        self._connected = True

    def disconnect(self):
        """Close the EDIABAS API connection."""
        if _LINUX:
            if self._connected:
                self._backend.disconnect()
                self._connected = False
            return

        if self._connected and self.api:
            self._apiEnd(self.handle)
            self._connected = False

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    def _setup_prototypes(self):
        """Set up ctypes function prototypes for all EDIABAS API functions.

        Uses getattr() to access DLL exports like __apiInit by their literal
        names, avoiding Python's double-underscore name mangling.
        """
        a = self.api

        fn = getattr(a, '__apiInit')
        fn.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        fn.restype = ctypes.c_int
        self._apiInit = fn

        fn = getattr(a, '__apiEnd')
        fn.argtypes = [ctypes.c_uint]
        fn.restype = None
        self._apiEnd = fn

        fn = getattr(a, '__apiJob')
        fn.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_char_p, ctypes.c_char_p]
        fn.restype = None
        self._apiJob = fn

        fn = getattr(a, '__apiState')
        fn.argtypes = [ctypes.c_uint]
        fn.restype = ctypes.c_int
        self._apiState = fn

        fn = getattr(a, '__apiStateExt')
        fn.argtypes = [ctypes.c_uint, ctypes.c_int]
        fn.restype = ctypes.c_int
        self._apiStateExt = fn

        fn = getattr(a, '__apiResultSets')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ushort)]
        fn.restype = ctypes.c_int
        self._apiResultSets = fn

        fn = getattr(a, '__apiResultNumber')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ushort),
                       ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultNumber = fn

        fn = getattr(a, '__apiResultName')
        fn.argtypes = [ctypes.c_uint, ctypes.c_char_p,
                       ctypes.c_ushort, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultName = fn

        fn = getattr(a, '__apiResultText')
        fn.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_char_p,
                       ctypes.c_ushort, ctypes.c_char_p]
        fn.restype = ctypes.c_int
        self._apiResultText = fn

        fn = getattr(a, '__apiResultReal')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_double),
                       ctypes.c_char_p, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultReal = fn

        fn = getattr(a, '__apiResultInt')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_short),
                       ctypes.c_char_p, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultInt = fn

        fn = getattr(a, '__apiResultWord')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ushort),
                       ctypes.c_char_p, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultWord = fn

        fn = getattr(a, '__apiResultLong')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_long),
                       ctypes.c_char_p, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultLong = fn

        fn = getattr(a, '__apiResultDWord')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong),
                       ctypes.c_char_p, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultDWord = fn

        fn = getattr(a, '__apiResultFormat')
        fn.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_int),
                       ctypes.c_char_p, ctypes.c_ushort]
        fn.restype = ctypes.c_int
        self._apiResultFormat = fn

        fn = getattr(a, '__apiErrorCode')
        fn.argtypes = [ctypes.c_uint]
        fn.restype = ctypes.c_int
        self._apiErrorCode = fn

        fn = getattr(a, '__apiErrorText')
        fn.argtypes = [ctypes.c_uint, ctypes.c_char_p, ctypes.c_int]
        fn.restype = None
        self._apiErrorText = fn

    def get_error(self):
        """Get the current error code and text."""
        if _LINUX:
            return (-1, "use EdiabasError.message for backend errors")
        code = self._apiErrorCode(self.handle)
        buf = ctypes.create_string_buffer(256)
        self._apiErrorText(self.handle, buf, 256)
        return code, buf.value.decode('latin-1', errors='replace')

    def run_job(self, sgbd, job, params="", result_filter="", timeout_ms=10000):
        """Run an EDIABAS job and return structured results.

        Returns a list of dicts, one per result set (set 0 = system, 1+ = data).
        Each dict maps result_name -> value (auto-typed).
        Raises EdiabasError on failure.
        """
        if _LINUX:
            return self._backend.run_job(sgbd, job, params, result_filter, timeout_ms)

        self._apiJob(self.handle, sgbd.encode(), job.encode(),
                     params.encode(), result_filter.encode())

        max_polls = timeout_ms // 100
        state = APIBUSY
        for _ in range(max_polls):
            state = self._apiStateExt(self.handle, 100)
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
        self._apiResultSets(self.handle, ctypes.byref(sets))

        result_sets = []
        for s in range(sets.value + 1):
            num = ctypes.c_ushort(0)
            self._apiResultNumber(self.handle, ctypes.byref(num), s)

            result = {}
            for i in range(num.value):
                name_buf = ctypes.create_string_buffer(64)
                if not self._apiResultName(self.handle, name_buf, i, s):
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
        self._apiResultFormat(self.handle, ctypes.byref(fmt), name_bytes, result_set)

        if fmt.value in (APIFORMAT_CHAR, APIFORMAT_BYTE):
            buf = ctypes.create_string_buffer(1024)
            if self._apiResultText(self.handle, buf, name_bytes, result_set, b""):
                return buf.value.decode('latin-1', errors='replace')
            return None

        elif fmt.value == APIFORMAT_REAL:
            v = ctypes.c_double(0)
            self._apiResultReal(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_INTEGER:
            v = ctypes.c_short(0)
            self._apiResultInt(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_WORD:
            v = ctypes.c_ushort(0)
            self._apiResultWord(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_LONG:
            v = ctypes.c_long(0)
            self._apiResultLong(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_DWORD:
            v = ctypes.c_ulong(0)
            self._apiResultDWord(self.handle, ctypes.byref(v), name_bytes, result_set)
            return v.value

        elif fmt.value == APIFORMAT_TEXT:
            buf = ctypes.create_string_buffer(1024)
            self._apiResultText(self.handle, buf, name_bytes, result_set, b"")
            return buf.value.decode('latin-1', errors='replace')

        elif fmt.value == APIFORMAT_BINARY:
            return None  # skip binary for now

        else:
            # Fallback: try text
            buf = ctypes.create_string_buffer(1024)
            if self._apiResultText(self.handle, buf, name_bytes, result_set, b""):
                return buf.value.decode('latin-1', errors='replace')
            return None

    def read_value(self, sgbd, job, result_name, result_set=1, params=""):
        """Run a job and read a single named result value.

        Convenience method for reading sensor values.
        Returns the value or None if not found.
        """
        if _LINUX:
            return self._backend.read_value(sgbd, job, result_name, result_set, params)

        self._apiJob(self.handle, sgbd.encode(), job.encode(),
                     params.encode(), b"")
        for _ in range(100):
            if self._apiStateExt(self.handle, 100) != APIBUSY:
                break

        # Try REAL first (most sensor values)
        v = ctypes.c_double(0)
        if self._apiResultReal(self.handle, ctypes.byref(v),
                               result_name.encode(), result_set):
            return v.value

        # Try WORD
        wv = ctypes.c_ushort(0)
        if self._apiResultWord(self.handle, ctypes.byref(wv),
                               result_name.encode(), result_set):
            return float(wv.value)

        # Try LONG
        lv = ctypes.c_long(0)
        if self._apiResultLong(self.handle, ctypes.byref(lv),
                               result_name.encode(), result_set):
            return float(lv.value)

        # Try TEXT
        tb = ctypes.create_string_buffer(256)
        if self._apiResultText(self.handle, tb, result_name.encode(),
                               result_set, b""):
            text = tb.value.decode('latin-1', errors='replace')
            try:
                return float(text)
            except ValueError:
                return text

        return None

    def read_results(self, sgbd, job, result_names, params="", result_set=1,
                      timeout_ms=10000):
        """Run a job and read multiple named results in one call.

        More efficient than calling read_value() multiple times when all
        results come from the same job. Returns dict of {name: value}.
        """
        if _LINUX:
            return self._backend.read_results(sgbd, job, result_names, params,
                                              result_set, timeout_ms)

        self._apiJob(self.handle, sgbd.encode(), job.encode(),
                     params.encode(), b"")

        max_polls = timeout_ms // 100
        state = APIBUSY
        for _ in range(max_polls):
            state = self._apiStateExt(self.handle, 100)
            if state != APIBUSY:
                break

        if state == APIERROR:
            code, text = self.get_error()
            raise EdiabasError(code, text)

        if state != APIREADY:
            raise EdiabasError(-2, f"Job timed out (state={state})")

        results = {}
        for name in result_names:
            name_b = name.encode()
            # Try REAL first (most sensor values)
            v = ctypes.c_double(0)
            if self._apiResultReal(self.handle, ctypes.byref(v),
                                   name_b, result_set):
                results[name] = v.value
                continue
            # Try TEXT
            buf = ctypes.create_string_buffer(1024)
            if self._apiResultText(self.handle, buf, name_b,
                                   result_set, b""):
                results[name] = buf.value.decode('latin-1', errors='replace')
                continue
            # Try WORD
            wv = ctypes.c_ushort(0)
            if self._apiResultWord(self.handle, ctypes.byref(wv),
                                   name_b, result_set):
                results[name] = float(wv.value)
                continue
            # Try LONG
            lv = ctypes.c_long(0)
            if self._apiResultLong(self.handle, ctypes.byref(lv),
                                   name_b, result_set):
                results[name] = float(lv.value)
                continue
            results[name] = None

        return results

    def list_jobs(self, sgbd):
        """List all available jobs for a given SGBD."""
        if _LINUX:
            return self._backend.list_jobs(sgbd)

        self._apiJob(self.handle, sgbd.encode(), b"_JOBS", b"", b"")

        for _ in range(100):
            if self._apiStateExt(self.handle, 100) != APIBUSY:
                break

        sets = ctypes.c_ushort(0)
        self._apiResultSets(self.handle, ctypes.byref(sets))

        jobs = []
        for s in range(1, sets.value + 1):
            buf = ctypes.create_string_buffer(1024)
            if self._apiResultText(self.handle, buf, b"JOBNAME", s, b""):
                jobs.append(buf.value.decode('latin-1', errors='replace'))
        return sorted(jobs)

    def identify(self, sgbd):
        """Run IDENT job and return identification data."""
        if _LINUX:
            return self._backend.identify(sgbd)
        results = self.run_job(sgbd, "IDENT")
        return results[1] if len(results) > 1 else {}

    def read_faults(self, sgbd):
        """Read fault codes (DTC). Returns list of fault dicts."""
        if _LINUX:
            return self._backend.read_faults(sgbd)
        results = self.run_job(sgbd, "FS_LESEN")
        faults = []
        for r in results[1:]:  # skip set 0
            if "F_ORT_NR" in r:
                faults.append(r)
            elif "JOB_STATUS" in r and r["JOB_STATUS"] == "OKAY":
                continue  # final status set
        return faults
