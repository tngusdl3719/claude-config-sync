#!/bin/bash
# Run on the server as root, after installing the forced command:
#   ssh <server> 'sudo install -m 755 /dev/stdin /usr/local/sbin/claudesync-git' < server/claudesync-git
#   ssh <server> 'sudo bash -s' < server/setup.sh
# Creates the claudesync account (git-shell only), its bare repo, and the key helper. Safe to run again.
set -euo pipefail

user=claudesync
repo=claude-config.git
helper=/usr/local/sbin/claudesync-add-key
forced=/usr/local/sbin/claudesync-git

[ -x "$forced" ] || { echo "install $forced first" >&2; exit 1; }

if ! id "$user" >/dev/null 2>&1; then
  useradd --create-home --shell /usr/bin/git-shell "$user"
fi
usermod --shell /usr/bin/git-shell "$user"
grep -qx /usr/bin/git-shell /etc/shells || echo /usr/bin/git-shell >> /etc/shells
home=$(getent passwd "$user" | cut -d: -f6)

chmod 750 "$home"
# git-shell (the login shell) runs a key's forced command only from here; root owns it.
install -d -m 755 -o root -g root "$home/git-shell-commands"
ln -sfn "$forced" "$home/git-shell-commands/claudesync-git"
install -d -m 700 -o "$user" -g "$user" "$home/.ssh"
touch "$home/.ssh/authorized_keys"
chown "$user:$user" "$home/.ssh/authorized_keys"
chmod 600 "$home/.ssh/authorized_keys"

if [ ! -d "$home/$repo" ]; then
  sudo -u "$user" git init -q --bare -b main "$home/$repo"
fi

cat > "$helper" <<EOF
#!/bin/bash
# Adds one ssh-ed25519 public key (read from stdin) for $user, restricted to git.
set -euo pipefail
IFS= read -r line || true
if ! printf '%s' "\$line" | grep -Eq '^ssh-ed25519 [A-Za-z0-9+/]+={0,3}( [A-Za-z0-9._@-]{1,64})?\$'; then
  echo "invalid key" >&2
  exit 2
fi
keys=$home/.ssh/authorized_keys
blob=\$(printf '%s' "\$line" | cut -d' ' -f2)
if grep -qF -- " \$blob" "\$keys"; then
  echo "already"
  exit 0
fi
printf 'restrict,command="claudesync-git" %s\n' "\$line" >> "\$keys"
echo "added"
EOF
chown root:root "$helper"
chmod 755 "$helper"
echo "ok: $user $home/$repo $helper"
