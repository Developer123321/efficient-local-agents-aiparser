# Included by writeShellApplication, which supplies strict mode and runtime tools.
# The host module defines `configured_state` (empty for the user's state directory).
state=${configured_state:-${XDG_STATE_HOME:-$HOME/.local/state}/python-vm}
unit=python-vm.service
usage() {
  echo "Usage: python-vm {start|stop|restart|status|logs|view|ssh [ARGUMENTS...]}" >&2
}
guest() {
  ssh -p 2222 -i "$state/id_ed25519" -o IdentitiesOnly=yes \
    -o UserKnownHostsFile="$state/known_hosts" -o StrictHostKeyChecking=accept-new \
    hackathon@127.0.0.1 "$@"
}
case "${1:-status}" in
start | stop | restart)
  [[ $# -le 1 ]] || { usage; exit 2; }
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
  for _ in $(seq 60); do
    if guest -o BatchMode=yes -o ConnectTimeout=2 true 2>/dev/null; then
      # One persistent terminal session, so a closed window loses no work.
      guest -t 'cd ~/workspace/python-vm 2>/dev/null; exec tmux new-session -A -s main'
      exit
    fi
    sleep 2
  done
  echo 'The guest did not accept SSH within two minutes; see python-vm logs.' >&2
  exit 1
  ;;
ssh)
  shift
  guest "$@"
  ;;
*)
  usage
  exit 2
  ;;
esac
