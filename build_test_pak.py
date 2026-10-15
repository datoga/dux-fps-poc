"""Create an original 320x200 Quake LMP console background and pack it as PAK."""
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 320, 200
pixels = bytearray([4] * (WIDTH * HEIGHT))

# Frame and low-res tiled background.
for y in range(0, HEIGHT, 8):
    for x in range(0, WIDTH, 8):
        c = 5 if ((x // 8 + y // 8) % 2) else 6
        for yy in range(y, min(y + 8, HEIGHT)):
            for xx in range(x, min(x + 8, WIDTH)):
                pixels[yy * WIDTH + xx] = c
for x in range(WIDTH):
    pixels[8 * WIDTH + x] = 15
    pixels[(HEIGHT - 9) * WIDTH + x] = 15
for y in range(8, HEIGHT - 8):
    pixels[y * WIDTH + 8] = 15
    pixels[y * WIDTH + WIDTH - 9] = 15

# Pixel penguin centered on a simple plinth.
def rect(x0, y0, x1, y1, color):
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[y * WIDTH + x] = color

# Dux-shaped mascot: outline, dark body, white belly, beak and feet.
rect(124, 62, 196, 154, 15)
rect(130, 66, 190, 148, 0)
rect(143, 98, 177, 144, 15)
rect(138, 76, 145, 83, 15)
rect(175, 76, 182, 83, 15)
rect(141, 77, 144, 80, 0)
rect(176, 77, 179, 80, 0)
rect(151, 86, 169, 91, 10)
rect(132, 150, 153, 156, 10)
rect(167, 150, 188, 156, 10)
# Minimal footer label drawn as pixel bars, avoiding copied typefaces/assets.
for x in range(110, 210, 10):
    rect(x, 170, x + 6, 174, 15)
    rect(x + 2, 176, x + 8, 180, 15)

lmp = struct.pack("<II", WIDTH, HEIGHT) + pixels
entries = [(b"gfx/conback.lmp", lmp)]

pak_path = ROOT / "dux-test.pak"
pak = bytearray(b"\0" * 12)
directory = bytearray()
for name, payload in entries:
    offset = len(pak)
    pak.extend(payload)
    directory.extend(name.ljust(56, b"\0") + struct.pack("<II", offset, len(payload)))
directory_offset = len(pak)
pak.extend(directory)
pak[:12] = b"PACK" + struct.pack("<II", directory_offset, len(directory))
pak_path.write_bytes(pak)
print(f"Wrote {pak_path} ({pak_path.stat().st_size} bytes)")
print(f"Asset: gfx/conback.lmp ({len(lmp)} bytes, {WIDTH}x{HEIGHT})")
