import argparse, shutil, sys, tempfile
from pathlib import Path
from jupyter_client.kernelspec import install_kernel_spec


def _run_kernel(argv):
    parser = argparse.ArgumentParser(prog="mojokernel")
    parser.add_argument("-f", "--connection-file", required=True)
    args = parser.parse_args(argv)
    from .kernel import MojoKernel
    from ipykernel.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=MojoKernel, argv=["-f", args.connection_file])


def _install_kernelspec(argv):
    parser = argparse.ArgumentParser(prog="mojokernel install")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--user", action="store_true", help="Install into user Jupyter dir")
    scope.add_argument("--sys-prefix", action="store_true", help="Install into current env")
    scope.add_argument("--prefix", help="Install into a given prefix")
    args = parser.parse_args(argv)

    prefix = args.prefix or (sys.prefix if args.sys_prefix else None)
    kernel_dir = Path(__file__).resolve().parent / "kernelspec"
    with tempfile.TemporaryDirectory() as tmpdir:
        dest = Path(tmpdir) / "mojo"
        shutil.copytree(kernel_dir, dest)
        install_kernel_spec(str(dest), kernel_name="mojo", user=bool(args.user), prefix=prefix, replace=True)
    print("Mojo kernel installed. Run `jupyter kernelspec list` to verify.")


def main():
    argv = sys.argv[1:]
    commands = {"install": _install_kernelspec, "run": _run_kernel}
    if argv and argv[0] in commands: commands[argv[0]](argv[1:])
    else: _run_kernel(argv)


if __name__ == "__main__": main()
