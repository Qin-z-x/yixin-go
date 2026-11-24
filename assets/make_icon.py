from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

size = 256
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)
draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=54, fill=(12, 15, 13, 255))
draw.rounded_rectangle((18, 18, size - 18, size - 18), radius=44, outline=(224, 196, 150, 120), width=2)
draw.rounded_rectangle((38, 38, size - 38, size - 38), radius=32, fill=(180, 70, 55, 255))
draw.rounded_rectangle((38, 38, size - 38, size - 38), radius=32, outline=(242, 219, 179, 220), width=3)
font = ImageFont.truetype(r"C:\Windows\Fonts\simkai.ttf", 124)
text = "弈"
bbox = draw.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1] - 5), text, font=font, fill=(247, 234, 211, 255))
Path("assets").mkdir(exist_ok=True)
img.save("assets/app-icon.png")
img.save("assets/app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon saved")
