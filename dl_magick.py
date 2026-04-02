import urllib.request, re, zipfile, os

print("Fetching binaries page...")
req = urllib.request.urlopen("https://imagemagick.org/archive/binaries/")
html = req.read().decode('utf-8')

match = re.search(r'href="(ImageMagick-7[^"]+portable-Q16-x64\.zip)"', html)
if not match:
    print("Could not find portable zip.")
    exit(1)

filename = match.group(1)
url = f"https://imagemagick.org/archive/binaries/{filename}"

print(f"Downloading {url}...")
urllib.request.urlretrieve(url, "im.zip")

print("Extracting...")
os.makedirs("imagemagick", exist_ok=True)
with zipfile.ZipFile("im.zip", 'r') as zip_ref:
    zip_ref.extractall("imagemagick")

os.remove("im.zip")
print("Done!")
