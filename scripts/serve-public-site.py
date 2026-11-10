"""Supervise the loopback preview; stop when its console exits or its deadline ends."""
import argparse
import ctypes
import os
import subprocess
import sys
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=int,default=14300)
    args=parser.parse_args()
    if not 2<=args.seconds<=14300: raise SystemExit('Preview lifetime must be 2–14300 seconds')
    if os.name!='nt': raise SystemExit('Use zensical serve directly on non-Windows development machines')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint,ctypes.c_int,ctypes.c_uint]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint]
    kernel.WaitForSingleObject.restype=ctypes.c_uint
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    parent=kernel.OpenProcess(0x00100000,False,os.getppid())  # SYNCHRONIZE; no signals
    if not parent: raise SystemExit('Cannot supervise the preview console')
    child=None
    try:
        child=subprocess.Popen([sys.executable,'-m','zensical','serve'],stdin=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        deadline=time.monotonic()+args.seconds
        while child.poll() is None:
            if time.monotonic()>=deadline or kernel.WaitForSingleObject(parent,0)!=0x102: break
            time.sleep(.25)
        if child.poll() not in (None,0): raise SystemExit('Zensical preview failed; inspect its retained log')
    finally:
        if child and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=3)
        kernel.CloseHandle(parent)


if __name__=='__main__': main()
