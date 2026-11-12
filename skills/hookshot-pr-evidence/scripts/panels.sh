#!/usr/bin/env bash
# One labelled evidence picture from several frames: each frame scaled to one size with its caption under it,
# N to a row. JPEG, ready for the evidence gist.
#
#   panels.sh <out.jpg> <per-row> <frame1> "<caption 1>" <frame2> "<caption 2>" ...
#   PANEL_W=1280 PANEL_H=720 panels.sh ...        (the size each frame is scaled to; default 1280x720)
#   PANEL_CROP=WxH+X+Y panels.sh ...              (crop every frame first: a menu's rows, an outline)
#
# Number the captions ("1. ...") and say in each what to look at; a reader sees the picture before the text.
# Look at the result before you cite it.
set -euo pipefail
out=$1; per_row=$2; shift 2
w=${PANEL_W:-1280}; h=${PANEL_H:-720}
font=$(fc-match -f '%{file}' 'DejaVu Sans')
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
i=0; row=(); rows=()
while [ $# -ge 2 ]; do
    frame=$1; caption=$2; shift 2
    i=$((i + 1))
    crop=()
    [ -n "${PANEL_CROP:-}" ] && crop=(-crop "$PANEL_CROP" +repage)
    magick "$frame" "${crop[@]}" -resize "${w}x${h}" -gravity center -background '#101010' -extent "${w}x${h}" \
        -gravity south -fill white -font "$font" -pointsize 26 -splice 0x44 -annotate +0+8 "$caption" "$tmp/p$i.png"
    row+=("$tmp/p$i.png")
    if [ ${#row[@]} -eq "$per_row" ]; then
        magick "${row[@]}" +append "$tmp/r${#rows[@]}.png"; rows+=("$tmp/r${#rows[@]}.png"); row=()
    fi
done
if [ ${#row[@]} -gt 0 ]; then
    magick "${row[@]}" +append "$tmp/r${#rows[@]}.png"; rows+=("$tmp/r${#rows[@]}.png")
fi
magick "${rows[@]}" -background '#101010' -append -quality 88 "$out"
magick identify -format '%f %wx%h %b\n' "$out"
