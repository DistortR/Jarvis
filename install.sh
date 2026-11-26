#!/bin/sh
set -eu

cd -P -- "$(dirname -- "$0")"

for executable in uv curl codex voxtype pw-record pw-play ffmpeg qs systemctl; do
    if ! command -v "$executable" >/dev/null 2>&1; then
        printf 'Jarvis setup requires %s on your PATH.\n' "$executable" >&2
        exit 1
    fi
done

if ! codex login status; then
    printf 'Sign in with codex login, then run install.sh again.\n' >&2
    exit 1
fi

umask 077
mkdir -p .jarvis/voices .jarvis/wake-models
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.jarvis/python"

if [ ! -x .wake-venv/bin/python ]; then
    uv python install 3.12
    uv venv --python 3.12 .wake-venv
fi

python="$PWD/.wake-venv/bin/python"
uv pip install --python "$python" -r requirements.txt scipy scikit-learn tqdm requests
# The ONNX-only environment does not need OpenWakeWord's TFLite dependency.
uv pip install --python "$python" --no-deps openwakeword==0.6.0

download_if_missing() (
    url=$1
    destination=$2
    if [ -s "$destination" ]; then
        exit 0
    fi
    temporary=$(mktemp "$destination.XXXXXX")
    trap 'rm -f -- "$temporary"' 0
    trap 'exit 1' HUP INT TERM
    curl --fail --location --silent --show-error --retry 2 "$url" -o "$temporary"
    test -s "$temporary"
    mv -f -- "$temporary" "$destination"
)

voice_base=https://huggingface.co/vishalrao8/piper-jarvis-high/resolve/main
for name in en_GB-jarvis-high.onnx en_GB-jarvis-high.onnx.json; do
    download_if_missing "$voice_base/$name" ".jarvis/voices/$name"
done

wake_base=https://github.com/dscripka/openWakeWord/releases/download/v0.5.1
for name in melspectrogram.onnx embedding_model.onnx; do
    download_if_missing "$wake_base/$name" ".jarvis/wake-models/$name"
done
download_if_missing \
    "https://raw.githubusercontent.com/TaterTotterson/Tater-Wake-Words/main/microWakeWordsV9/jarvis.oww.onnx" \
    ".jarvis/wake-models/jarvis.oww.onnx"

installed_models=$(voxtype setup model --list)
if ! printf '%s\n' "$installed_models" | grep -Eq '^[[:space:]]*base\.en[[:space:]]'; then
    voxtype setup --download --model base.en --no-post-install
fi

"$python" -B -m unittest -q
"$python" -B -c 'from pathlib import Path; from wake import WakeDetector; WakeDetector(Path(".jarvis/wake-models"))'

systemctl --user daemon-reload
systemctl --user enable "$PWD/jarvis.service"
if systemctl --user is-active --quiet graphical-session.target jarvis.service; then
    systemctl --user restart jarvis.service
    systemctl --user is-active jarvis.service
else
    printf 'Jarvis is installed and will start at your next graphical login.\n'
fi
