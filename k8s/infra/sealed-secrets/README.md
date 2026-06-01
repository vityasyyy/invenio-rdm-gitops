# Sealed Secrets

Sealed Secrets is used to encrypt Kubernetes Secret values in Git.

## Key Management

The sealed-secrets private and public keys are stored at `~/.sealed-secrets/` on the admin workstation — **not** in this repository. This is critical for disaster recovery.

For backup, the keys should be stored in a secure secrets manager or offline backup.

## Disaster Recovery

If the Sealed Secrets controller loses its keys, all SealedSecret resources will need to be re-encrypted with new keys after restoring or regenerating the key pair.