#!/usr/bin/env bash
# Download, build, and install the NexCLIP Recorder media stack.
#
#   sudo ./bin/nexrec-install-media.sh all
#   sudo ./bin/nexrec-install-media.sh ffmpeg
#   sudo ./bin/nexrec-install-media.sh mediamtx
#   sudo ./bin/nexrec-install-media.sh decklink-status
#
# Pins (Ubuntu 24.04):
#   FFmpeg 9.0.2     https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
#   MediaMTX v1.21.1 official linux tarball (not built from source)
#   nv-codec-headers 13.1.15.0  (only if an NVIDIA toolkit is detected)
#
# FFmpeg lands in ${NEXREC_FFMPEG_PREFIX:-/usr/local} so ffmpeg and ffprobe
# are on the systemd PATH. DeckLink (--enable-decklink) is turned on when
# Blackmagic SDK headers are found. Desktop Video drivers (the package that
# creates /dev/blackmagic) are a separate install and are not downloaded here.
#
# Idempotent. Rebuild with NEXREC_FORCE_FFMPEG_BUILD=1.
# Replace an existing MediaMTX binary with NEXREC_FORCE_MEDIAMTX=1.
# Replace /etc/nexrec/mediamtx.yml with NEXREC_FORCE_MEDIAMTX_CONFIG=1.
#
# SDK headers: NEXREC_DECKLINK_SDK or DECKLINK_SDK (SDK root or the directory
# that contains DeckLinkAPI.h and DeckLinkAPIDispatch.cpp). Also checks
# /usr/include, /usr/local/include, /opt/decklink-sdk, /usr/src/decklink-sdk,
# and "Blackmagic DeckLink SDK *" under /opt, /usr/src, and /usr/local/src.
# NVENC: autodetect nvidia-smi, /usr/local/cuda, CUDA_HOME, or ffnvcodec
# headers. NEXREC_ENABLE_NVENC=1 forces headers on; =0 leaves NVENC off.
# A missing GPU does not fail the build.

NEXREC_FFMPEG_VERSION=9.0.2
NEXREC_FFMPEG_URL=https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz
NEXREC_FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e

NEXREC_MEDIAMTX_VERSION=v1.21.1

NEXREC_NVCODEC_VERSION=13.1.15.0
NEXREC_NVCODEC_URL=https://github.com/FFmpeg/nv-codec-headers/releases/download/n13.1.15.0/nv-codec-headers-13.1.15.0.tar.gz
NEXREC_NVCODEC_SHA256=52532ceade3d5c1af62624986f13cf01b63c910576b08c0c278756c5e4b41ad0

nexrec_media_log() { printf '[nexrec-media] %s\n' "$*"; }
nexrec_media_warn() { printf '[nexrec-media] WARN %s\n' "$*" >&2; }

nexrec_flag_on() {
  local v="${1:-}"
  v="${v,,}"
  [[ "$v" == "1" || "$v" == "true" || "$v" == "yes" || "$v" == "on" ]]
}

nexrec_mediamtx_arch_of() {
  case "$1" in
    x86_64 | amd64) printf '%s\n' amd64 ;;
    aarch64 | arm64) printf '%s\n' arm64 ;;
    armv7l | armv7) printf '%s\n' armv7 ;;
    armv6l | armv6) printf '%s\n' armv6 ;;
    *) return 1 ;;
  esac
}

nexrec_mediamtx_sha256() {
  case "$1" in
    amd64) printf '%s\n' 653abc672a3e693f8d3b2717752492fdcfb8072291ec108d03d3dd857411b0ee ;;
    arm64) printf '%s\n' 6a3aa635fb60ea9b8d566ec306f0a42ff1b6b52a3942bc2baffbe55880d4c3dd ;;
    armv6) printf '%s\n' 47c0f461693e27df1fa6b3cf91dc550224fabffb96e9e7ec458c5f112779bdf4 ;;
    armv7) printf '%s\n' d032bc572a07a139417db9584a33b91d48c7eb8f27e447645f25c259447e9c07 ;;
    *) return 1 ;;
  esac
}

nexrec_mediamtx_url() {
  local arch="$1"
  printf 'https://github.com/bluenviron/mediamtx/releases/download/%s/mediamtx_%s_linux_%s.tar.gz\n' \
    "$NEXREC_MEDIAMTX_VERSION" "$NEXREC_MEDIAMTX_VERSION" "$arch"
}

nexrec_ffmpeg_desired_stamp() {
  local decklink="$1" nvenc="$2" fdk="$3" srt="$4" zvbi="$5" prefix="$6"
  printf 'version=%s prefix=%s decklink=%s nvenc=%s fdk=%s srt=%s zvbi=%s\n' \
    "$NEXREC_FFMPEG_VERSION" "$prefix" "$decklink" "$nvenc" "$fdk" "$srt" "$zvbi"
}

nexrec_stamp_bit() {
  local stamp="$1" key="$2"
  if [[ "$stamp" =~ (^|[[:space:]])"${key}"=([01]) ]]; then
    printf '%s\n' "${BASH_REMATCH[2]}"
  else
    printf '%s\n' 0
  fi
}

# build = compile; skip = installed binary already matches the pin and features.
nexrec_ffmpeg_decide() {
  local have="$1" verline="$2" conf="$3" stamp="$4" desired="$5" force="$6"
  local escaped bit key flag
  if [[ "$force" == "1" ]]; then
    printf '%s\n' build
    return 0
  fi
  if [[ "$have" != "1" ]]; then
    printf '%s\n' build
    return 0
  fi
  escaped="${NEXREC_FFMPEG_VERSION//./\\.}"
  if [[ ! "$verline" =~ ffmpeg[[:space:]]version[[:space:]]${escaped}([^0-9]|$) ]]; then
    printf '%s\n' build
    return 0
  fi
  for flag in --enable-gpl --enable-nonfree --enable-libx264 --enable-openssl; do
    if [[ "$conf" != *"$flag"* ]]; then
      printf '%s\n' build
      return 0
    fi
  done
  local -a pairs=(decklink:--enable-decklink nvenc:--enable-nvenc fdk:--enable-libfdk-aac srt:--enable-libsrt zvbi:--enable-libzvbi)
  for pair in "${pairs[@]}"; do
    key="${pair%%:*}"
    flag="${pair#*:}"
    bit="$(nexrec_stamp_bit "$desired" "$key")"
    if [[ "$bit" == "1" && "$conf" != *"$flag"* ]]; then
      printf '%s\n' build
      return 0
    fi
  done
  if [[ -n "$stamp" && "$stamp" != "$desired" ]]; then
    printf '%s\n' build
    return 0
  fi
  printf '%s\n' skip
}

# install = fetch the pin; skip = leave the current binary alone.
nexrec_mediamtx_decide() {
  local have="$1" stamp="$2" pinned="$3" force="$4"
  if [[ "$force" == "1" || "$have" != "1" ]]; then
    printf '%s\n' install
    return 0
  fi
  if [[ "$stamp" == "$pinned" ]]; then
    printf '%s\n' skip
    return 0
  fi
  if [[ -n "$stamp" ]]; then
    printf '%s\n' install
    return 0
  fi
  printf '%s\n' skip
}

nexrec_config_action() {
  local path="$1" force="$2"
  if [[ ! -f "$path" || "$force" == "1" ]]; then
    printf '%s\n' write
  else
    printf '%s\n' keep
  fi
}

nexrec_unit_action() {
  local path="$1" force="$2"
  if [[ ! -f "$path" || "$force" == "1" ]]; then
    printf '%s\n' write
    return 0
  fi
  if grep -q 'nexrec-managed' "$path"; then
    printf '%s\n' write
    return 0
  fi
  printf '%s\n' keep
}

nexrec_decklink_include_dir() {
  local p="$1" child
  [[ -n "$p" && -d "$p" ]] || return 1
  if [[ -f "$p/DeckLinkAPI.h" && -f "$p/DeckLinkAPIDispatch.cpp" ]]; then
    printf '%s\n' "$p"
    return 0
  fi
  if [[ -f "$p/Linux/include/DeckLinkAPI.h" && -f "$p/Linux/include/DeckLinkAPIDispatch.cpp" ]]; then
    printf '%s\n' "$p/Linux/include"
    return 0
  fi
  if [[ -f "$p/include/DeckLinkAPI.h" && -f "$p/include/DeckLinkAPIDispatch.cpp" ]]; then
    printf '%s\n' "$p/include"
    return 0
  fi
  for child in "$p"/*; do
    [[ -d "$child" ]] || continue
    if [[ -f "$child/Linux/include/DeckLinkAPI.h" && -f "$child/Linux/include/DeckLinkAPIDispatch.cpp" ]]; then
      printf '%s\n' "$child/Linux/include"
      return 0
    fi
  done
  return 1
}

nexrec_find_decklink_include() {
  local c found
  local -a cands=()
  [[ -n "${NEXREC_DECKLINK_SDK:-}" ]] && cands+=("$NEXREC_DECKLINK_SDK")
  [[ -n "${DECKLINK_SDK:-}" ]] && cands+=("$DECKLINK_SDK")
  if [[ -n "${NEXREC_DECKLINK_SDK_CANDIDATES:-}" ]]; then
    local -a extra=()
    IFS=':' read -r -a extra <<< "${NEXREC_DECKLINK_SDK_CANDIDATES}"
    cands+=("${extra[@]}")
  fi
  if [[ "${NEXREC_DECKLINK_SCAN_SYSTEM:-1}" != "0" ]]; then
    cands+=(
      /usr/include
      /usr/local/include
      /opt/decklink-sdk
      /usr/src/decklink-sdk
    )
  fi
  for c in "${cands[@]}"; do
    if found="$(nexrec_decklink_include_dir "$c")"; then
      printf '%s\n' "$found"
      return 0
    fi
  done
  if [[ "${NEXREC_DECKLINK_SCAN_SYSTEM:-1}" == "0" ]]; then
    return 1
  fi
  local base
  for base in /opt /usr/src /usr/local/src; do
    [[ -d "$base" ]] || continue
    for c in "$base"/Blackmagic\ DeckLink\ SDK* "$base"/decklink-sdk*; do
      [[ -e "$c" ]] || continue
      if found="$(nexrec_decklink_include_dir "$c")"; then
        printf '%s\n' "$found"
        return 0
      fi
    done
  done
  return 1
}

nexrec_nvenc_wanted() {
  local v="${NEXREC_ENABLE_NVENC-}"
  if [[ -n "$v" ]]; then
    nexrec_flag_on "$v"
    return
  fi
  if [[ "${NEXREC_NVENC_PROBE:-}" == "1" ]]; then
    return 0
  fi
  if [[ "${NEXREC_NVENC_PROBE:-}" == "0" ]]; then
    return 1
  fi
  if command -v nvidia-smi >/dev/null 2>&1; then
    return 0
  fi
  if [[ -d /usr/local/cuda || -n "${CUDA_HOME:-}" || -n "${CUDA_PATH:-}" ]]; then
    return 0
  fi
  if [[ -f /usr/include/ffnvcodec/nvEncodeAPI.h || -f /usr/local/include/ffnvcodec/nvEncodeAPI.h ]]; then
    return 0
  fi
  return 1
}

nexrec_download() {
  local url="$1" dest="$2" sha="$3"
  mkdir -p "$(dirname "$dest")"
  if [[ -f "$dest" ]] && echo "${sha}  ${dest}" | sha256sum -c - >/dev/null 2>&1; then
    nexrec_media_log "cached $(basename "$dest")"
    return 0
  fi
  rm -f "$dest" "${dest}.partial"
  nexrec_media_log "download $url"
  curl -fL --retry 3 --retry-delay 2 -o "${dest}.partial" "$url"
  mv "${dest}.partial" "$dest"
  echo "${sha}  ${dest}" | sha256sum -c -
}

nexrec_try_pkg() {
  local pkg="$1"
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    return 0
  fi
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$pkg"
}

nexrec_install_build_deps() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq ca-certificates curl xz-utils
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
  if [[ "${ID:-}" == "ubuntu" ]]; then
    apt-get install -y -qq software-properties-common
    add-apt-repository -y universe
    add-apt-repository -y multiverse
    apt-get update -qq
  fi
  apt-get install -y -qq build-essential pkg-config nasm libx264-dev libssl-dev
  local pkg
  for pkg in yasm libfdk-aac-dev libsrt-dev libzvbi-dev; do
    if nexrec_try_pkg "$pkg"; then
      nexrec_media_log "package $pkg"
    else
      nexrec_media_warn "package $pkg not installed; that feature will be left out of this FFmpeg"
    fi
  done
  pkg-config --exists x264 || {
    nexrec_media_warn "libx264 development files are required (--enable-libx264)"
    return 1
  }
  pkg-config --exists openssl || {
    nexrec_media_warn "OpenSSL development files are required (--enable-openssl)"
    return 1
  }
}

nexrec_install_nvcodec() {
  local prefix="$1" work="$2"
  local tar="$work/nv-codec-headers-${NEXREC_NVCODEC_VERSION}.tar.gz"
  nexrec_download "$NEXREC_NVCODEC_URL" "$tar" "$NEXREC_NVCODEC_SHA256"
  rm -rf "$work/nv-codec-headers-${NEXREC_NVCODEC_VERSION}"
  tar -xzf "$tar" -C "$work"
  make -C "$work/nv-codec-headers-${NEXREC_NVCODEC_VERSION}" install PREFIX="$prefix"
  if [[ ! -f "$prefix/include/ffnvcodec/nvEncodeAPI.h" ]]; then
    return 1
  fi
}

nexrec_install_ffmpeg() {
  local prefix="${NEXREC_FFMPEG_PREFIX:-/usr/local}"
  local work="${NEXREC_BUILD_DIR:-/var/tmp/nexrec-build}"
  local stamp_dir="${NEXREC_STAMP_DIR:-/var/lib/nexrec/build-stamps}"
  local stamp_file="$stamp_dir/ffmpeg"
  local bin="$prefix/bin/ffmpeg"
  local probe="$prefix/bin/ffprobe"
  local jobs="${NEXREC_BUILD_JOBS:-$(nproc)}"
  local sdk="" have=0 verline="" conf="" stamp="" force=0
  local want_nvenc=0 want_fdk=0 want_srt=0 want_zvbi=0 want_deck=0
  local desired action

  nexrec_install_build_deps
  mkdir -p "$work" "$stamp_dir" "$prefix/bin"

  if sdk="$(nexrec_find_decklink_include)"; then
    want_deck=1
    nexrec_media_log "DeckLink SDK headers: $sdk (--enable-decklink)"
  else
    nexrec_media_warn "DeckLink SDK headers not found. Building IP-capable FFmpeg without --enable-decklink."
    nexrec_media_warn "Set DECKLINK_SDK or NEXREC_DECKLINK_SDK to the SDK root or the include dir with DeckLinkAPI.h."
    nexrec_media_warn "Desktop Video drivers (/dev/blackmagic) are separate from those SDK headers and are not installed here."
  fi

  if nexrec_nvenc_wanted; then
    if nexrec_install_nvcodec "$prefix" "$work"; then
      want_nvenc=1
      nexrec_media_log "NVENC headers ${NEXREC_NVCODEC_VERSION} installed (no GPU required to compile)"
    else
      nexrec_media_warn "NVENC headers failed to install; continuing without --enable-nvenc"
    fi
  else
    nexrec_media_log "NVENC left off (no NVIDIA toolkit detected; NEXREC_ENABLE_NVENC=1 forces it)"
  fi

  pkg-config --exists fdk-aac && want_fdk=1 || nexrec_media_warn "fdk-aac not found; FFmpeg native AAC encoder will be used"
  pkg-config --exists 'srt >= 1.3.0' && want_srt=1 || nexrec_media_warn "libsrt not found; SRT will be omitted"
  pkg-config --exists zvbi-0.2 && want_zvbi=1 || nexrec_media_warn "libzvbi not found; zvbi captions will be omitted"

  desired="$(nexrec_ffmpeg_desired_stamp "$want_deck" "$want_nvenc" "$want_fdk" "$want_srt" "$want_zvbi" "$prefix")"
  nexrec_flag_on "${NEXREC_FORCE_FFMPEG_BUILD:-}" && force=1
  if [[ -x "$bin" ]]; then
    have=1
    verline="$("$bin" -version 2>/dev/null | head -n 1 || true)"
    conf="$("$bin" -buildconf 2>/dev/null || true)"
  fi
  if [[ -f "$stamp_file" ]]; then
    stamp="$(tr -d '\n' < "$stamp_file")"
  fi
  action="$(nexrec_ffmpeg_decide "$have" "$verline" "$conf" "$stamp" "$desired" "$force")"
  if [[ "$action" == "skip" ]]; then
    printf '%s\n' "$desired" > "$stamp_file"
    nexrec_media_log "FFmpeg ${NEXREC_FFMPEG_VERSION} already matches ($bin). Set NEXREC_FORCE_FFMPEG_BUILD=1 to rebuild."
    return 0
  fi

  local tarball="$work/ffmpeg-${NEXREC_FFMPEG_VERSION}.tar.xz"
  local src="$work/ffmpeg-${NEXREC_FFMPEG_VERSION}"
  nexrec_download "$NEXREC_FFMPEG_URL" "$tarball" "$NEXREC_FFMPEG_SHA256"
  rm -rf "$src"
  tar -xJf "$tarball" -C "$work"

  local -a args=(
    --prefix="$prefix"
    --bindir="${prefix}/bin"
    --libdir="${prefix}/lib"
    --enable-shared
    --disable-static
    --enable-rpath
    --disable-doc
    --disable-ffplay
    --enable-gpl
    --enable-nonfree
    --enable-libx264
    --enable-openssl
  )
  [[ "$want_fdk" == "1" ]] && args+=(--enable-libfdk-aac)
  [[ "$want_srt" == "1" ]] && args+=(--enable-libsrt)
  [[ "$want_zvbi" == "1" ]] && args+=(--enable-libzvbi)
  [[ "$want_nvenc" == "1" ]] && args+=(--enable-ffnvcodec --enable-nvenc)
  if [[ "$want_deck" == "1" ]]; then
    if [[ "$sdk" == *" "* ]]; then
      nexrec_media_warn "DeckLink SDK path contains spaces ($sdk). Rename the folder and re-run; building without DeckLink."
      want_deck=0
      desired="$(nexrec_ffmpeg_desired_stamp "$want_deck" "$want_nvenc" "$want_fdk" "$want_srt" "$want_zvbi" "$prefix")"
    else
      args+=(--enable-decklink --extra-cflags="-I${sdk}")
    fi
  fi

  nexrec_media_log "configure FFmpeg ${NEXREC_FFMPEG_VERSION}: ${args[*]}"
  (
    cd "$src"
    export PKG_CONFIG_PATH="${prefix}/lib/pkgconfig${PKG_CONFIG_PATH:+:}${PKG_CONFIG_PATH:-}"
    ./configure "${args[@]}"
    make -j"$jobs"
    make install
  )
  if [[ -d "${prefix}/lib" ]]; then
    printf '%s\n' "${prefix}/lib" > /etc/ld.so.conf.d/nexrec-ffmpeg.conf
    ldconfig || nexrec_media_warn "ldconfig failed; rpath on the binaries should still find ${prefix}/lib"
  fi
  [[ -x "$bin" && -x "$probe" ]] || {
    nexrec_media_warn "install did not produce $bin and $probe"
    return 1
  }
  verline="$("$bin" -version 2>/dev/null | head -n 1 || true)"
  conf="$("$bin" -buildconf 2>/dev/null || true)"
  if [[ "$(nexrec_ffmpeg_decide 1 "$verline" "$conf" "" "$desired" 0)" != "skip" ]]; then
    nexrec_media_warn "installed FFmpeg does not match the requested feature set"
    "$bin" -version || true
    return 1
  fi
  printf '%s\n' "$desired" > "$stamp_file"
  nexrec_media_log "installed $bin"
  nexrec_media_log "$verline"
  if [[ "$want_deck" == "1" ]]; then
    nexrec_media_log "DeckLink capture is enabled. Drivers still come from the Blackmagic Desktop Video package."
  fi
}

nexrec_install_mediamtx() {
  local prefix="${NEXREC_FFMPEG_PREFIX:-/usr/local}"
  local work="${NEXREC_BUILD_DIR:-/var/tmp/nexrec-build}"
  local stamp_dir="${NEXREC_STAMP_DIR:-/var/lib/nexrec/build-stamps}"
  local stamp_file="$stamp_dir/mediamtx"
  local bin="$prefix/bin/mediamtx"
  local yml_src yml_dest="/etc/nexrec/mediamtx.yml"
  local unit_src unit_dest="/etc/systemd/system/mediamtx.service"
  local root arch sha url have=0 stamp="" force=0 cfg_force=0
  local action cfg_action unit_action

  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  yml_src="$root/mediamtx.yml"
  unit_src="$root/systemd/mediamtx.service"
  [[ -f "$yml_src" && -f "$unit_src" ]] || {
    nexrec_media_warn "missing $yml_src or $unit_src"
    return 1
  }
  arch="$(nexrec_mediamtx_arch_of "$(uname -m)")" || {
    nexrec_media_warn "no MediaMTX binary for $(uname -m)"
    return 1
  }
  sha="$(nexrec_mediamtx_sha256 "$arch")"
  url="$(nexrec_mediamtx_url "$arch")"
  mkdir -p "$work" "$stamp_dir" "$(dirname "$bin")" /etc/nexrec

  nexrec_flag_on "${NEXREC_FORCE_MEDIAMTX:-}" && force=1
  nexrec_flag_on "${NEXREC_FORCE_MEDIAMTX_CONFIG:-}" && cfg_force=1
  [[ -x "$bin" ]] && have=1
  if [[ -f "$stamp_file" ]]; then
    stamp="$(tr -d '\n' < "$stamp_file")"
  fi
  action="$(nexrec_mediamtx_decide "$have" "$stamp" "$NEXREC_MEDIAMTX_VERSION" "$force")"
  if [[ "$action" == "skip" && "$have" == "1" && -z "$stamp" ]]; then
    nexrec_media_warn "leaving existing $bin in place (not installed by this script). NEXREC_FORCE_MEDIAMTX=1 replaces it with ${NEXREC_MEDIAMTX_VERSION}."
  elif [[ "$action" == "skip" ]]; then
    nexrec_media_log "MediaMTX ${NEXREC_MEDIAMTX_VERSION} already installed ($bin)"
  else
    local tarball="$work/mediamtx_${NEXREC_MEDIAMTX_VERSION}_linux_${arch}.tar.gz"
    local unpack="$work/mediamtx-unpack"
    nexrec_download "$url" "$tarball" "$sha"
    rm -rf "$unpack"
    mkdir -p "$unpack"
    tar -xzf "$tarball" -C "$unpack" mediamtx
    install -m 755 "$unpack/mediamtx" "$bin"
    printf '%s\n' "$NEXREC_MEDIAMTX_VERSION" > "$stamp_file"
    nexrec_media_log "installed $bin (${NEXREC_MEDIAMTX_VERSION})"
  fi

  cfg_action="$(nexrec_config_action "$yml_dest" "$cfg_force")"
  if [[ "$cfg_action" == "write" ]]; then
    install -m 644 "$yml_src" "$yml_dest"
    nexrec_media_log "wrote $yml_dest (RTSP 127.0.0.1:8554, WHEP :8889, paths in0–in9)"
  else
    nexrec_media_log "keeping existing $yml_dest (NEXREC_FORCE_MEDIAMTX_CONFIG=1 overwrites)"
  fi

  unit_action="$(nexrec_unit_action "$unit_dest" "$force")"
  if [[ "$unit_action" == "write" ]]; then
    sed "s|/usr/local/bin/mediamtx|${prefix}/bin/mediamtx|g" "$unit_src" > "$unit_dest"
    chmod 644 "$unit_dest"
    nexrec_media_log "wrote $unit_dest"
  else
    nexrec_media_warn "keeping existing $unit_dest (not nexrec-managed). NEXREC_FORCE_MEDIAMTX=1 replaces it."
  fi

  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    if [[ "$unit_action" == "write" ]]; then
      systemctl enable --now mediamtx.service
      nexrec_media_log "enabled mediamtx.service"
    elif [[ "$action" == "install" ]]; then
      systemctl try-restart mediamtx.service || nexrec_media_warn "mediamtx.service was not restarted"
    fi
  else
    nexrec_media_warn "systemctl not available; start mediamtx with: $bin $yml_dest"
  fi
}

nexrec_install_decklink_status() {
  local prefix="${NEXREC_FFMPEG_PREFIX:-/usr/local}"
  local root sdk
  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if ! sdk="$(nexrec_find_decklink_include)"; then
    nexrec_media_warn "skipped nexrec-decklink-status (no DeckLink SDK headers). Capture still needs those headers at FFmpeg build time."
    return 0
  fi
  if [[ "$sdk" == *" "* ]]; then
    nexrec_media_warn "skipped nexrec-decklink-status; SDK path contains spaces: $sdk"
    return 0
  fi
  make -C "$root/tools/decklink-status" SDK="$sdk"
  make -C "$root/tools/decklink-status" SDK="$sdk" PREFIX="$prefix" install
  nexrec_media_log "installed ${prefix}/bin/nexrec-decklink-status"
}

nexrec_install_media_all() {
  nexrec_install_ffmpeg
  nexrec_install_mediamtx
  nexrec_install_decklink_status
}

if [[ "${NEXREC_INSTALL_SOURCE_ONLY:-}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

cmd="${1:-all}"
case "$cmd" in
  all) nexrec_install_media_all ;;
  ffmpeg) nexrec_install_ffmpeg ;;
  mediamtx) nexrec_install_mediamtx ;;
  decklink-status) nexrec_install_decklink_status ;;
  *)
    echo "usage: $0 [all|ffmpeg|mediamtx|decklink-status]" >&2
    exit 2
    ;;
esac
