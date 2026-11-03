"""Release-only automation. Never reads or publishes the BlueBit application source."""
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
import zipfile

MAX_BYTES = 250 * 1024 * 1024
CHUNK_BYTES = 6 * 1024 * 1024
repo = os.environ["GITHUB_REPOSITORY"]
staging_url = os.environ.get("STAGING_MANIFEST_URL", "")
source_url = os.environ.get("SOURCE_URL", "")


def read_json(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return json.loads(response.read(65536))


if staging_url:
    parsed = urllib.parse.urlparse(staging_url)
    if (
        parsed.scheme != "https"
        or not re.fullmatch(r"[a-z0-9]+\.supabase\.co", parsed.hostname or "")
        or not re.fullmatch(
            r"/storage/v1/object/public/bluebit-downloads/apk-staging/[a-f0-9-]{36}/manifest\.json",
            parsed.path,
        )
        or parsed.query
    ):
        raise ValueError("Invalid staging manifest URL")
    metadata = read_json(staging_url)
    version = str(metadata["version"])
    build_code = int(metadata["versionCode"])
    expected_size = int(metadata["fileBytes"])
    part_count = int(metadata["partCount"])
    if not 0 < expected_size <= MAX_BYTES:
        raise ValueError("APK exceeds the allowed size")
    if part_count != (expected_size + CHUNK_BYTES - 1) // CHUNK_BYTES:
        raise ValueError("Invalid APK part count")
    prefix = staging_url.rsplit("/", 1)[0]
    sources = [f"{prefix}/part-{i:05d}.apk" for i in range(part_count)]
else:
    if not re.fullmatch(r"https://expo\.dev/artifacts/eas/[A-Za-z0-9_-]+\.apk", source_url):
        raise ValueError("Only a finished Expo APK URL is accepted for an initial import")
    version = os.environ.get("APP_VERSION", "")
    build_code = int(os.environ.get("APP_VERSION_CODE", "0"))
    expected_size = None
    sources = [source_url]

if not re.fullmatch(r"\d+\.\d+\.\d+(?:[.-][A-Za-z0-9]+)*", version) or build_code < 1:
    raise ValueError("Invalid version or Android build code")

latest = subprocess.run(
    ["gh", "api", f"repos/{repo}/releases/latest"],
    capture_output=True,
    text=True,
)
if latest.returncode == 0:
    latest_tag = json.loads(latest.stdout)["tag_name"]
    match = re.fullmatch(r"v.+-build(\d+)", latest_tag)
    if match and int(match.group(1)) >= build_code:
        raise ValueError("The build code must be higher than the current public release")
elif "404" not in latest.stderr:
    raise RuntimeError("Could not verify the current public release")

target = os.path.join(os.environ["RUNNER_TEMP"], "BlueBit.apk")
total = 0
digest = hashlib.sha256()
with open(target, "wb") as output:
    for index, url in enumerate(sources):
        part_bytes = 0
        with urllib.request.urlopen(url, timeout=120) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                part_bytes += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("APK exceeds the maximum size")
                output.write(chunk)
                digest.update(chunk)
        if staging_url:
            expected_part = min(CHUNK_BYTES, expected_size - index * CHUNK_BYTES)
            if part_bytes != expected_part:
                raise ValueError(f"APK part {index} is incomplete")

if expected_size is not None and total != expected_size:
    raise ValueError("The assembled APK size does not match the upload")
with zipfile.ZipFile(target) as archive:
    if "AndroidManifest.xml" not in archive.namelist() or archive.testzip() is not None:
        raise ValueError("The file is not a valid, intact Android APK")

manifest = {
    "version": version,
    "versionCode": build_code,
    "downloadUrl": f"https://github.com/{repo}/releases/latest/download/BlueBit.apk",
    "downloadPageUrl": "https://bluebitoffical.com/",
    "apkBytes": total,
    "sha256": digest.hexdigest(),
}
with open(os.path.join(os.environ["RUNNER_TEMP"], "latest.json"), "w") as output:
    json.dump(manifest, output, indent=2)
with open(os.environ["GITHUB_ENV"], "a") as output:
    output.write(f"RELEASE_TAG=v{version}-build{build_code}\n")
    output.write(f"RELEASE_VERSION={version}\n")
    output.write(f"RELEASE_BUILD_CODE={build_code}\n")
print(f"Verified APK: {total} bytes, SHA-256 {digest.hexdigest()}")
