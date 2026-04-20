#!/bin/bash

root='/media/root/'

duplicity='/media/data/app/python/venv-duplicity/bin/duplicity'
#--asynchronous-upload  --volsize 500 --use-agent
main_opts='--num-retries 30 --no-compression --progress --volsize 5000 --use-agent'
key_opts='--encrypt-key 98A03ECC388F63D8ECD8E45F6B092ED14EB88A1F --encrypt-key 0DC13AF988AEB1CADEA87A935F4F9F6FD54A7520 --encrypt-key 119C988EDB9A776FECE6F04F9F080F93FB7E32E1'
gpg_opts="-z 0"
s3_opts="--s3-multipart-max-procs 10 --s3-region-name us-east-1 --s3-endpoint-url http://192.168.55.100:30157"

export AWS_ACCESS_KEY_ID="NgEhyyFmzt4qd6id0nkcNSOrV"
export AWS_SECRET_ACCESS_KEY="1ZeepuvgORW6cGN9Rm6SyboOP"
export AWS_REQUEST_CHECKSUM_CALCULATION=when_required
export AWS_RESPONSE_CHECKSUM_VALIDATION=when_required

bucket='backup-laptop'

export GPG_TTY="$(tty)"
gpg-connect-agent UPDATESTARTUPTTY /bye >/dev/null

#gpgconf --kill gpg-agent >/dev/null 2>&1 || true
#gpgconf --launch gpg-agent >/dev/null 2>&1 || true

btrfs subvolume delete "${root}"'@boot_bak'
btrfs subvolume snapshot -r "${root}"'@boot' "${root}"'@boot_bak'
sync
${duplicity} ${main_opts} ${key_opts} ${s3_opts} --gpg-options "${gpg_opts}" "${root}"'@boot_bak' boto3+s3://${bucket}/boot
#btrfs subvolume delete "${root}"'@boot_bak'
sync

read -p 'Press [Enter] key to continue...'

btrfs subvolume delete "${root}"'@debian_bak'
btrfs subvolume snapshot -r "${root}"'@debian' "${root}"'@debian_bak'
sync
${duplicity} ${main_opts} ${key_opts} ${s3_opts} --gpg-options "${gpg_opts}" "${root}"'@debian_bak' boto3+s3://${bucket}/debian
#btrfs subvolume delete "${root}"'@debian_bak'
sync

read -p 'Press [Enter] key to continue...'

btrfs subvolume delete "${root}"'@data_bak'
btrfs subvolume snapshot -r "${root}"'@data' "${root}"'@data_bak'
sync
${duplicity} ${main_opts} ${key_opts} ${s3_opts} --gpg-options "${gpg_opts}" "${root}"'@data_bak' boto3+s3://${bucket}/data
#btrfs subvolume delete "${root}"'@data_bak'
sync

read -p 'Press [Enter] key to continue...'

${duplicity} ${main_opts} ${key_opts} ${s3_opts} --gpg-options "${gpg_opts}" "${root}"'@virtual' boto3+s3://${bucket}/virtual

read -p 'Press [Enter] key to continue...'

btrfs subvolume delete "${root}"'@secrets_bak'
btrfs subvolume snapshot -r "${root}"'@secrets' "${root}"'@secrets_bak'
sync
${duplicity} ${main_opts} ${key_opts} ${s3_opts} --gpg-options "${gpg_opts}" "${root}"'@secrets_bak' boto3+s3://${bucket}/secrets
#btrfs subvolume delete "${root}"'@secrets_bak'
sync

unset AWS_ACCESS_KEY_ID
unset AWS_SECRET_ACCESS_KEY

read -p 'Press [Enter] key to continue...'


# remove-older-than 3M --force --dry-run
# collection-status
