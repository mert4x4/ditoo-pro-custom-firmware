"""./ditoo: install the custom firmware on a Divoom Ditoo Pro, or go back to stock.

  ./ditoo scan                 find the Ditoo Pro and print its address
  ./ditoo version              read the installed firmware version
  ./ditoo build [--stock F]    download stock 24020 from Divoom, build 24025-remote, check it
  ./ditoo install [--stock F]  build, check and flash 24025-remote (about 15 minutes)
  ./ditoo restore [--stock F]  flash stock 24020 back
  ./ditoo run SCRIPT [ARGS]    run a Python script with the client library (client/)

Options: --address AA:BB:CC:DD:EE:FF (or DITOO_ADDRESS) picks the device instead of
scanning; --stock FILE uses a 24020.MVA you already have instead of downloading it;
--yes skips the confirmation; --log FILE also writes the flash log to FILE.
"""
import argparse, asyncio, os, runpy, sys
from pathlib import Path

FW = Path(__file__).resolve().parent
sys.path.insert(0, str(FW))
sys.path.insert(0, str(FW.parent / "client"))

import images  # noqa: E402

WARNING = """
This replaces the firmware of your Ditoo Pro with an unofficial, modified one.
It is not made, tested or supported by Divoom, and it may void your warranty.
A failed update can in the worst case leave the device unusable. Use at your own risk.

  - keep the Ditoo on USB power for the whole update (about 15 minutes)
  - close the Divoom app, or turn off Bluetooth on your phone
  - keep this computer close to the Ditoo and do not let it sleep
"""


def confirm(args, what):
    print(WARNING)
    if args.yes:
        return
    answer = input(f"Type 'yes' to {what}: ").strip().lower()
    if answer != "yes":
        sys.exit("cancelled, nothing was written")


def address(args):
    return args.address or os.environ.get("DITOO_ADDRESS") or None


def prepare(args):
    images.fetch_stock(args.stock)
    out = images.build()
    from verify import verify
    if not verify(out, images.stock_path(), quiet=True):
        sys.exit("the built image failed its checks; nothing will be flashed")
    print("all image checks passed")
    return out


async def cmd_scan(args):
    from ditoo_api import find_device
    dev = await find_device()
    print(f"{dev.name}  {dev.address}")


async def cmd_version(args):
    import flash
    v = await flash.read_version(address(args))
    labels = {images.SUPPORTED["stock"]["version"]: "stock",
              images.SUPPORTED["remote"]["version"]: "custom (remote mode)"}
    print(f"installed firmware: {v} {labels.get(v, '')}".rstrip())


async def cmd_install(args):
    out = prepare(args)
    confirm(args, "flash the custom firmware")
    import flash
    ok = await flash.flash(out.read_bytes(), images.SUPPORTED["remote"]["version"], address(args),
                           log=flash.Log(args.log))
    sys.exit(0 if ok else 1)


async def cmd_restore(args):
    path = images.fetch_stock(args.stock)
    confirm(args, "flash stock firmware 24020 back")
    import flash
    ok = await flash.flash(path.read_bytes(), images.SUPPORTED["stock"]["version"], address(args),
                           allow_downgrade=True, log=flash.Log(args.log))
    sys.exit(0 if ok else 1)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        if len(sys.argv) < 3:
            sys.exit("usage: ./ditoo run SCRIPT [ARGS]")
        sys.argv = sys.argv[2:]
        runpy.run_path(sys.argv[0], run_name="__main__")
        return
    ap = argparse.ArgumentParser(prog="./ditoo", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("command", choices=["scan", "version", "build", "install", "restore"])
    ap.add_argument("--address")
    ap.add_argument("--stock", metavar="FILE")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--log", metavar="FILE")
    args = ap.parse_args()
    try:
        if args.command == "build":
            prepare(args)
            print(f"ready to flash: firmware/out/{images.SUPPORTED['remote']['file']}")
        else:
            asyncio.run(globals()["cmd_" + args.command](args))
    except images.ImageError as e:
        sys.exit(f"error: {e}")
    except KeyboardInterrupt:
        sys.exit("interrupted")
    except Exception as e:
        if os.environ.get("DITOO_DEBUG"):
            raise
        msg = f"error: {e or type(e).__name__}"
        if isinstance(e, (FileNotFoundError, ConnectionError)) or "org.bluez" in str(e):
            msg += "\nBluetooth is not available. Is BlueZ installed and running (systemctl status bluetooth)?"
        sys.exit(msg + "\n(set DITOO_DEBUG=1 for the full error)")


if __name__ == "__main__":
    main()
