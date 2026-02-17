import subprocess,re

def _get():
    try:
        out = subprocess.check_output(['mojo', '--version'], text=True, stderr=subprocess.DEVNULL)
        m = re.search(r'(\d+\.\d+\.\d+\.\d+)', out)
        if m: return m.group(1)
    except FileNotFoundError:
        raise RuntimeError("mojo not found on PATH — install Mojo before building mojokernel")
    raise RuntimeError(f"Could not parse version from: mojo --version")

__version__ = _get()
