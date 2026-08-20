# Timelabs Control — key setup (for the person assembling the bundle)

The launcher scripts here (`Timelabs Control.command`, etc.) SSH into the
server as `root` using a private key named `timelabs_hermes`.

**That private key is NOT — and must never be — committed to this repo.**
A private key in git is the same as publishing the server's root password:
anyone who can read the repo can log in as root. The key is delivered to the
owner's Mac out-of-band instead.

## Assembling a bundle to hand to the owner

1. Generate a fresh keypair (once per machine you're setting up):

   ```bash
   ssh-keygen -t ed25519 -f ./timelabs_hermes -C "timelabs-mac-launcher" -N ""
   ```

   This writes `timelabs_hermes` (private) and `timelabs_hermes.pub` (public)
   into this folder. Git ignores `timelabs_hermes` (see `.gitignore`), so it
   will not be committed.

2. Authorize the public half on the server, then delete the `.pub` locally:

   ```bash
   ssh root@<server> 'cat >> ~/.ssh/authorized_keys' < ./timelabs_hermes.pub
   rm ./timelabs_hermes.pub
   ```

3. Zip this `mac-bundle/` folder (now containing the private `timelabs_hermes`)
   and send it to the owner over a trusted channel (AirDrop, a password
   manager's secure note, an encrypted message) — never email or chat it, and
   never `git add` it.

4. The owner runs `Install Timelabs Control.command`, which copies the key to
   `~/.ssh/` and puts the launchers on the Desktop. They should then delete the
   downloaded bundle.

## Rotating / revoking a key

If a key is ever exposed (committed, laptop lost, shared by mistake), treat it
as compromised and rotate immediately — deleting it from a repo does NOT undo
the exposure, because it is still in git history and in any clone.

```bash
# On the server: remove the compromised public key from authorized_keys,
# then add the replacement. Keep at least one working key in the file so you
# don't lock yourself out — verify a second session still connects before
# closing your current one.
ssh root@<server>
vi ~/.ssh/authorized_keys        # delete the old timelabs-mac-launcher line
# ...append the new public key (step 2 above), save, keep this session open
# until a fresh login with the new key succeeds.
```
