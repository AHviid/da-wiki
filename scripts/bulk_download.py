#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "tqdm"]
# ///
"""Download the latest da.wikipedia article dump (multistream + index)."""

import argparse
import hashlib
import re
import sys
from pathlib import Path

import httpx
from tqdm import tqdm

BASE = "https://dumps.wikimedia.org/dawiki"
UA = "dawiki-dump-fetcher/0.1 (contact: ballebyhviid@hotmail.com)"
FILES = [
    "dawiki-{d}-pages-articles-multistream.xml.bz2",
    "dawiki-{d}-pages-articles-multistream-index.txt.bz2",
]


def latest_date(c: httpx.Client) -> str:
    dates = re.findall(r'href="(\d{8})/"', c.get(f"{BASE}/").text)
    for d in sorted(dates, reverse=True):
        status = c.get(f"{BASE}/{d}/dumpstatus.json").json()
        if status.get("jobs", {}).get("articlesmultistreamdump", {}).get("status") == "done":
            return d
    sys.exit("no completed dump found")


def sha1sums(c: httpx.Client, date: str) -> dict[str, str]:
    txt = c.get(f"{BASE}/{date}/dawiki-{date}-sha1sums.txt").text
    return {name: h for h, name in (l.split() for l in txt.splitlines() if l.strip())}


def download(c: httpx.Client, url: str, dest: Path) -> None:
    pos = dest.stat().st_size if dest.exists() else 0
    headers = {"Range": f"bytes={pos}-"} if pos else {}
    with c.stream("GET", url, headers=headers) as r:
        if r.status_code == 416:
            return
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0)) + pos
        with open(dest, "ab") as f, tqdm(
            total=total, initial=pos, unit="B", unit_scale=True, desc=dest.name
        ) as bar:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                bar.update(len(chunk))


def sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-o", "--outdir", type=Path, default=Path("dumps"))
    p.add_argument("-d", "--date", help="YYYYMMDD (default: latest completed)")
    p.add_argument("--no-verify", action="store_true")
    a = p.parse_args()

    with httpx.Client(headers={"User-Agent": UA}, follow_redirects=True, timeout=60) as c:
        date = a.date or latest_date(c)
        print(f"dump: {date}")
        outdir = a.outdir / date
        outdir.mkdir(parents=True, exist_ok=True)
        sums = {} if a.no_verify else sha1sums(c, date)

        for tmpl in FILES:
            name = tmpl.format(d=date)
            dest = outdir / name
            download(c, f"{BASE}/{date}/{name}", dest)
            if name in sums:
                print(f"  verifying {name}...", end=" ", flush=True)
                print("ok" if sha1(dest) == sums[name] else "MISMATCH")

    print(outdir)


if __name__ == "__main__":
    main()