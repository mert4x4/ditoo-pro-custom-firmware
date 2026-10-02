"""Getting the stock image and building the custom one from it.

No Divoom firmware is stored in this repository. The stock 24020 image is
downloaded from Divoom's own servers (or taken from a file you already have) and
accepted only when its SHA-1 matches supported.json. Everything else is built
from it on your machine:

    stock 24020 --tools/unlock.py--> 24021-unlock --remote/prebuilt--> 24025-remote

Only 24025-remote is written and flashed. 24021-unlock is an intermediate step in
memory: it carries the unlocked update gate that 24025-remote keeps, so the device
can later go back to stock 24020. Each step checks the SHA-1 of its result, so the image you flash is byte for byte
the one that was tested.
"""
import hashlib, json, sys, urllib.request
from pathlib import Path

FW = Path(__file__).resolve().parent
OUT = FW / "out"
SUPPORTED = json.loads((FW / "supported.json").read_text())
sys.path.insert(0, str(FW / "tools"))
import mva_pack, unlock  # noqa: E402


class ImageError(Exception):
    pass


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _download(url: str, log) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ditoo-fw"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _api_urls(log):
    api = SUPPORTED["stock"]["update_api"]
    for body in api["requests"]:
        try:
            req = urllib.request.Request(api["url"], data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "User-Agent": "ditoo-fw"})
            with urllib.request.urlopen(req, timeout=30) as r:
                answer = json.loads(r.read())
        except Exception as e:
            log(f"  update API: {e}")
            continue
        if answer.get("Sha1") == SUPPORTED["stock"]["sha1"] and answer.get("FileId"):
            for host in api["files"]:
                yield host + answer["FileId"]


def stock_path() -> Path:
    return OUT / SUPPORTED["stock"]["file"]


def fetch_stock(source=None, log=print) -> Path:
    """Put the stock image in out/. `source` is a local file; None downloads it."""
    want = SUPPORTED["stock"]["sha1"]
    dest = stock_path()
    OUT.mkdir(exist_ok=True)
    if source:
        data = Path(source).read_bytes()
        if sha1(data) != want:
            raise ImageError(f"{source} is not stock firmware {SUPPORTED['stock']['version']} "
                             f"(SHA-1 {sha1(data)}, expected {want})")
        dest.write_bytes(data)
        log(f"stock image taken from {source}")
        return dest
    if dest.exists() and sha1(dest.read_bytes()) == want:
        log(f"stock image already in {dest.relative_to(FW.parent)}")
        return dest
    tried = set()
    for url in [*SUPPORTED["stock"]["urls"], *_api_urls(log)]:
        if url in tried:
            continue
        tried.add(url)
        log(f"downloading {url}")
        try:
            data = _download(url, log)
        except Exception as e:
            log(f"  failed: {e}")
            continue
        if sha1(data) != want:
            log(f"  wrong file (SHA-1 {sha1(data)})")
            continue
        dest.write_bytes(data)
        log(f"stock image saved to {dest.relative_to(FW.parent)} (SHA-1 ok)")
        return dest
    raise ImageError("could not download the stock image from Divoom's servers. If you have a copy of "
                     f"{SUPPORTED['stock']['file']} (SHA-1 {want}), pass it with --stock FILE.")


def build(log=print) -> Path:
    """stock -> (24021-unlock in memory) -> out/24025-remote.MVA."""
    src = stock_path()
    if not src.exists():
        fetch_stock(log=log)
    stock = src.read_bytes()
    if sha1(stock) != SUPPORTED["stock"]["sha1"]:
        raise ImageError(f"{src} is not the stock image; delete it and run fetch again")

    base = unlock.unlock(stock)     # checks its own SHA-1

    pre = FW / "remote/prebuilt"
    meta = json.loads((pre / "remote.json").read_text())
    blob = (pre / meta["blob"]).read_bytes()
    if sha1(blob) != meta["blob_sha1"]:
        raise ImageError("remote/prebuilt/remote.bin does not match remote.json; run tools/build_remote.py")
    if meta["base_sha1"] != sha1(base):
        raise ImageError("remote/prebuilt/remote.json was made for a different base image")
    img, report = mva_pack.pack(base, meta["patches"], blob, int(meta["blob_addr"], 16))
    out = OUT / SUPPORTED["remote"]["file"]
    out.write_bytes(img)
    (OUT / (out.stem + ".json")).write_text(json.dumps(report, indent=2) + "\n")
    if sha1(img) != SUPPORTED["remote"]["sha1"]:
        log(f"NOTE: {out.name} differs from the released build (SHA-1 {sha1(img)}). "
            "That is expected only if you changed remote.c.")
    else:
        log(f"built {out.name} (SHA-1 {sha1(img)}, same as the released build)")
    return out
