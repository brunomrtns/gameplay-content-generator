#!/bin/bash

set -e

export LOCAL_DIR
LOCAL_DIR=$(pwd)
export PACKAGE_VERSION="1.0.0"
export PACKAGE_NAME="gpcg-panel"
export PACKAGE_RELEASE="0"
export REPOSITORY_PATH="adev"
export TARGET_DISTRO
export TARGET_DISTRO_VERSION

# ── Verificar pré-requisitos ─────────────────────────────────────────────────

# Verificar se o venv do GPCG existe com textual
GPCG_VENV="${GPCG_VENV:-${LOCAL_DIR}/.venv/bin/python}"
if [[ ! -f "${GPCG_VENV}" ]]; then
  echo ""
  echo "  ⚠  ATENÇÃO: venv do GPCG não encontrado em ${GPCG_VENV}"
  echo ""
  echo "  O gpcg-panel precisa do venv do projeto com textual instalado."
  echo "  Para configurar:"
  echo ""
  echo "    1. ./scripts/dev.sh setup"
  echo "    2. .venv/bin/pip install textual"
  echo ""
  exit 1
fi

# Verificar se textual está instalado
if ! "${GPCG_VENV}" -c "import textual" 2>/dev/null; then
  echo ""
  echo "  ⚠  ATENÇÃO: textual não está instalado no venv"
  echo ""
  echo "  Instale com: .venv/bin/pip install textual"
  echo ""
  exit 1
fi

TARGET_DISTRO="$(grep -E '^NAME=' /etc/os-release | grep -o "\"[a-z,A-Z]*" | grep -o "[a-z,A-Z]*" | tr "[:upper:]" "[:lower:]")"
if [ "${TARGET_DISTRO}" = 'centos' ] || [ "${TARGET_DISTRO}" = 'rocky' ]; then TARGET_DISTRO="el"; fi
if [ "${TARGET_DISTRO}" = 'fedora' ]; then TARGET_DISTRO="fc"; fi
echo "TARGET_DISTRO=\"${TARGET_DISTRO}\""

if [ "${TARGET_DISTRO}" = 'ubuntu' ] || [ "${TARGET_DISTRO}" = 'debian' ]; then TARGET_DISTRO_VERSION="$(grep -E '^VERSION=' /etc/os-release | grep -o "([a-z,A-Z]*" | grep -o "[a-z,A-Z]*" | tr "[:upper:]" "[:lower:]")"; fi
if [ "${TARGET_DISTRO}" = 'fc' ] || [ "${TARGET_DISTRO}" = 'el' ]; then TARGET_DISTRO_VERSION="$(grep -E '^VERSION=' /etc/os-release | grep -o "\"[0-9]*" | grep -o "[0-9]*")"; fi
echo "TARGET_DISTRO_VERSION=\"${TARGET_DISTRO_VERSION}\""

function apt_install() {
	echo "Criando pacote."
	./make_deb_package
	echo "Instalando pacote."
	dpkg -i "artifacts/${REPOSITORY_PATH}/${TARGET_DISTRO}/${TARGET_DISTRO_VERSION}/${PACKAGE_NAME}_${PACKAGE_VERSION}-${REPOSITORY_PATH}.${PACKAGE_RELEASE}_amd64.deb" || apt -f -y install
	echo "Excluindo artifacts."
	rm -rf artifacts
	rm -Rf ~/debbuild
}

function apt_uninstall() {
	echo "Removendo pacote."
	apt -y autoremove "${PACKAGE_NAME}"
}

if apt --version; then
	if [[ "${1}" != "uninstall" ]]; then
		apt_install
	else
		apt_uninstall
	fi
else
	echo "Only apt/dpkg is supported for gpcg-panel."
	exit 1
fi

echo "Fim!!!"
