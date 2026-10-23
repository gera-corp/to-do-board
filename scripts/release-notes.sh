#!/usr/bin/env bash
# Печатает описание релиза: раздел CHANGELOG.md для версии и как её поставить.
# Использование: scripts/release-notes.sh 1.2.3
set -euo pipefail
version="${1#v}"
cd "$(dirname "$0")/.."

notes=$(awk -v v="$version" '
  $0 ~ "^## \\[" v "\\]" { found = 1; next }
  found && /^## \[/ { exit }
  found && /^\[[^]]+\]: / { exit }
  found { print }
' CHANGELOG.md | sed -e '/./,$!d')

if [ -z "$notes" ]; then
  echo "В CHANGELOG.md нет раздела для версии $version — добавьте его перед релизом." >&2
  exit 1
fi

cat <<NOTES
$notes

### Install
- Image: \`ghcr.io/gera-corp/to-do-board:$version\` (linux/amd64, linux/arm64)
- Helm chart: \`helm install to-do-board oci://ghcr.io/gera-corp/charts/to-do-board --version $version\`
NOTES
