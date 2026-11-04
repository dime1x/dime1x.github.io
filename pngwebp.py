from PIL import Image
import os

input_dir = "img"
output_dir = "webp"

os.makedirs(output_dir, exist_ok=True)

for filename in os.listdir(input_dir):
    if filename.lower().endswith(".png"):
        img = Image.open(
            os.path.join(input_dir, filename)
        )

        output = os.path.join(
            output_dir,
            os.path.splitext(filename)[0] + ".webp"
        )

        img.save(output, "WEBP", lossless=True, method=6)

        old_size = os.path.getsize(
            os.path.join(input_dir, filename)
        )
        new_size = os.path.getsize(output)

        print(
            f"{filename}: "
            f"{old_size / 1024:.1f} KB -> "
            f"{new_size / 1024:.1f} KB"
        )