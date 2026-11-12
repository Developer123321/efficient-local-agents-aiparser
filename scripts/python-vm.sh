# Included by writeShellApplication, which supplies strict mode and runtime tools.
# The host module defines `configured_state` (empty for the user's state directory).
state=${configured_state:-${XDG_STATE_HOME:-$HOME/.local/state}/python-vm}
unit=python-vm.service
usage() {
  echo "Usage: python-vm {start|stop|restart|status|logs|view}" >&2
}
[[ $# -le 1 ]] || { usage; exit 2; }
case "${1:-status}" in
start | stop | restart)
  exec systemctl --user "$1" "$unit"
  ;;
status)
  exec systemctl --user status --no-pager "$unit"
  ;;
logs)
  exec journalctl --user -u "$unit" -n 80 --no-pager
  ;;
view)
  if ! systemctl --user is-active --quiet "$unit"; then
    echo 'The VM is not running. Start it with python-vm start.' >&2
    exit 1
  fi
  # A relative address keeps the socket path within the kernel's length limit.
  cd -- "$state/run"
  for _ in $(seq 30); do
    [[ -S console.sock ]] && break
    sleep 1
  done
  if [[ ! -S console.sock ]]; then
    echo 'The guest console is unavailable; see python-vm logs.' >&2
    exit 1
  fi
  echo 'Guest console; press Enter for a prompt, Ctrl-] to detach. The session keeps running.'
  exec socat STDIO,raw,echo=0,escape=0x1d UNIX-CONNECT:console.sock
  ;;
*)
  usage
  exit 2
  ;;
esac
