# Running the pipeline on AWS

The point of this is to make a story without the laptop being involved. Everything
up to the render happens on the server, from a browser, including on a phone. The
render comes home.

## What runs where

| Stage | Where | Why |
|---|---|---|
| `new`, `write`, `critique`, redraft | server | Text and API calls. Already there. |
| `record` | server | Needs ffmpeg, but only to encode mp3 from PCM. Cheap in memory. |
| `design` | server | Downloads images. Cheap in memory. |
| `direct` | **laptop** | ffmpeg renders a clip per shot, then joins them. The box has 412 MiB of RAM; this would not finish. |
| Upload to YouTube | laptop | Manual anyway — nothing here uploads yet. |

The box is a **t3.nano in us-east-1a**: 2 vCPU, 412 MiB RAM, 20 GB root disk.
It stays that size. `stories/` moves onto its own 10 GB volume, and S3 holds a
copy of everything git ignores.

Three places, and the rule for which is which: git holds text, S3 holds the
binaries that cost money to make, and the volume is where the pipeline works.

| | Holds | If it vanished |
|---|---|---|
| **git** | Prose, outlines, manifests, reviews, bibles, prompts, ledger | Everything, and it is also on GitHub and the laptop |
| **S3** | `audio/`, `images/`, the SFX cache, and video you push from the laptop | Re-narrating and re-illustrating one 30-minute story is real money |
| **EBS volume** | `stories/`, the working copy | An hour, if the last sweep ran |

## What it costs

| | Per month |
|---|---|
| t3.nano | ~$3.80, already running |
| 10 GB gp3 volume | $0.80 |
| S3, audio + images | ~150 MB per story, so about $0.04 for ten stories |
| S3, rendered video (optional) | 1.9 GB per story at Infrequent Access, about $0.02 per story |
| Transfer out | First 100 GB a month is free; a story's assets are 150 MB |

Under $6 a month until the video backups pile up. List prices — check an invoice.

---

## 1. The volume

Same AZ as the instance, or it cannot attach.

```bash
aws ec2 create-volume --size 10 --volume-type gp3 \
  --availability-zone us-east-1a \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Name,Value=mw-stories}]'

aws ec2 attach-volume --volume-id vol-XXXX --instance-id i-XXXX --device /dev/sdf
```

On the instance. It is a Nitro box, so `/dev/sdf` shows up as `/dev/nvme1n1`:

```bash
lsblk                        # the 10G device with no MOUNTPOINT
sudo file -s /dev/nvme1n1    # must say exactly: /dev/nvme1n1: data
```

If that reports a filesystem instead of `data`, stop — `mkfs` on the wrong device
destroys the box.

Format it, copy the eight story bundles across, then mount it in their place:

```bash
sudo mkfs -t ext4 /dev/nvme1n1
sudo mkdir -p /mnt/stories
sudo mount /dev/nvme1n1 /mnt/stories
sudo rsync -a /srv/middlewatch/stories/ /mnt/stories/
sudo chown -R ubuntu:ubuntu /mnt/stories
sudo umount /mnt/stories
sudo mount /dev/nvme1n1 /srv/middlewatch/stories
```

Check before trusting it. The repo should see the same files it saw before:

```bash
df -h /srv/middlewatch/stories                 # 10G, not /dev/root
git -C /srv/middlewatch status --short stories # nothing unexpected
```

`git status` reporting deletions means the copy did not land — unmount and look
again. The originals are still underneath the mountpoint until you delete them,
which is worth leaving alone for a week.

Survive reboots, by UUID rather than device name:

```bash
sudo blkid /dev/nvme1n1
sudo cp /etc/fstab /etc/fstab.bak
echo 'UUID=<paste>  /srv/middlewatch/stories  ext4  defaults,nofail  0  2' | sudo tee -a /etc/fstab
sudo mount -a          # must return cleanly before you reboot
```

`nofail` is not optional. Without it a detached volume drops the instance into
emergency mode at boot, and you lose ssh to the box the review queue runs on.

## 2. The bucket

```bash
aws s3api create-bucket --bucket middlewatch-assets --region us-east-1
aws s3api put-public-access-block --bucket middlewatch-assets \
  --public-access-block-configuration '{"BlockPublicAcls":true,"IgnorePublicAcls":true,"BlockPublicPolicy":true,"RestrictPublicBuckets":true}'
aws s3api put-bucket-versioning --bucket middlewatch-assets \
  --versioning-configuration Status=Enabled
```

Versioning before the first upload. It is what makes an overwritten narration
recoverable, and it cannot be applied retroactively.

Versioning also means every re-record keeps the old take forever, so expire the
superseded ones:

```bash
aws s3api put-bucket-lifecycle-configuration --bucket middlewatch-assets \
  --lifecycle-configuration '{"Rules":[{"ID":"expire-noncurrent","Status":"Enabled","Filter":{},"NoncurrentVersionExpiration":{"NoncurrentDays":30},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'
```

Thirty days to notice a mistake, then the old version goes.

## 3. Access, without keys on the box

EC2 can hand the instance temporary credentials, so nothing is stored on disk.

```bash
cat > /tmp/trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
 "Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF

cat > /tmp/policy.json <<'EOF'
{"Version":"2012-10-17","Statement":[
 {"Sid":"ListTheBucket","Effect":"Allow",
  "Action":["s3:ListBucket","s3:GetBucketLocation"],
  "Resource":"arn:aws:s3:::middlewatch-assets"},
 {"Sid":"ReadWriteObjects","Effect":"Allow",
  "Action":["s3:PutObject","s3:GetObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],
  "Resource":"arn:aws:s3:::middlewatch-assets/*"}]}
EOF

aws iam create-role --role-name mw-instance --assume-role-policy-document file:///tmp/trust.json
aws iam put-role-policy --role-name mw-instance --policy-name mw-assets \
  --policy-document file:///tmp/policy.json
aws iam create-instance-profile --instance-profile-name mw-instance
aws iam add-role-to-instance-profile --instance-profile-name mw-instance --role-name mw-instance
aws ec2 associate-iam-instance-profile --instance-id i-XXXX \
  --iam-instance-profile Name=mw-instance
```

**No `s3:DeleteObject`.** A wrong path can add objects but not remove them, which
is the second layer under "mw-sync never passes `--delete`".

The laptop needs its own credentials — an IAM user with the same policy, then
`aws configure`. Create the access key in the console and paste it in yourself;
it is the one secret here that cannot be avoided.

## 4. ffmpeg and the CLI on the server

```bash
sudo apt update && sudo apt install -y ffmpeg awscli
ffmpeg -version
aws sts get-caller-identity     # should name mw-instance, not a user
aws s3 ls s3://middlewatch-assets/
```

`record` shells out to ffmpeg to encode each chapter, and fails with a clear
message if it is missing — but it fails *after* the narration is paid for, so
install it before the first record.

The apt CLI is v1. `s3 sync` is all that is used here and v1 does it fine.

## 5. mw-sync and the hourly sweep

```bash
cd /srv/middlewatch
sudo install -m 755 scripts/deploy/mw-sync /usr/local/bin/
sudo install -m 644 scripts/deploy/mw-sync.service scripts/deploy/mw-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mw-sync.timer

mw-sync -n push-all          # dry run: what it would upload
systemctl list-timers mw-sync.timer
```

`mw-sync --help` lists the commands. It refuses to run against a directory with
no bundles in it rather than reporting a successful backup of nothing.

## 6. Moving what is already on the laptop

The laptop has 2 GB of finished work, most of it one rendered video. Push it all
up, then pull down only what the server needs — it has no use for video and the
volume is 10 GB:

```bash
mw-sync push-all                              # on the laptop
```
```bash
mw-sync --kinds audio,images pull-all         # on the server
```

## 7. The loop, from anywhere

1. Open `review.middlewatch.co`. Approve the outline, write, read the chapters,
   approve them. Then **Record** and **Design** — both show what they will cost
   before they start, and both keep running if you close the tab.
2. **Assets .zip** hands you the audio and images. Or, on a machine with
   credentials, `mw-sync --kinds audio,images pull <slug>` instead.
3. On the laptop, render:
   ```bash
   git pull                                 # the prose, from the server's commits
   unzip ~/Downloads/<slug>-assets.zip -d stories/
   python3 -m story_pipeline.cli direct stories/<slug>
   ```
4. Upload the mp4 by hand, then `mw-sync push <slug>` to back up the master.

Nothing in steps 1 and 2 needs the laptop, which is the point of all of this.

## 8. Rendering on AWS later

Worth doing when the channel earns enough to notice the bill, not before: a box
that can render costs more per month than everything above put together.

The seam is narrow. `cmd_direct` in `story_pipeline/cli.py` calls
`director.direct()`, and everything it reads — `audio/`, `images/`, the timelines
and the outline — is already on the volume and in S3. Nothing else in the
pipeline touches ffmpeg's expensive path. So the change is where that one call
runs, not what it does:

- **Spot instance, started for the job.** A c7a.large at roughly $0.05/hr spot
  renders a 30-minute story in minutes. Start it, `git clone`, `mw-sync pull`,
  `cli direct`, `mw-sync push`, terminate. A shell script, no code change.
- **Container on ECS or Batch.** Same steps, a queue in front, and worth it only
  when several stories are waiting at once.

Either way the render reads from S3 and writes back to it, which is why video has
a place in the bucket layout even though nothing on the server produces it today.

## 9. Restoring onto a fresh box

```bash
git clone <repo> /srv/middlewatch && cd /srv/middlewatch
python3 -m venv .venv && .venv/bin/python3 -m pip install -r requirements.txt
cp config.example.yaml config.yaml    # then edit
cp .env.example .env                  # then paste the three keys
# section 1 for the volume, section 3 for the role, section 4 for ffmpeg
mw-sync --kinds audio,images pull-all
```

If something is missing after that, it was stored somewhere this document does
not describe. Fix where it is written, not the restore.

## Gotchas

- **The volume and the instance must share an AZ** (`us-east-1a`). A volume
  cannot attach across zones, and a snapshot restored elsewhere is a new volume.
- **10 GB is about 60 stories** of audio and images. Video never lands on the
  server. Check with `df -h /srv/middlewatch/stories`; grow with
  `aws ec2 modify-volume --size 20` then `sudo resize2fs /dev/nvme1n1`, which
  works while it is mounted.
- **Never `--delete`.** Not on a sync, not to tidy up. The IAM policy blocks it,
  and that is deliberate belt and braces rather than redundancy.
- **Do not run `direct` on the nano.** 412 MiB of RAM against an x264 encode: it
  will swap, then be killed, after burning CPU credits.
- **Do not push mid-stage.** A half-written mp3 uploads happily; the next sweep
  corrects it, but a restore in between gets the truncated file.
- **Renaming a story's slug orphans its S3 prefix.** The slug is the key. The old
  objects stay and cost money until removed from the console.
- **Run everything from the repo root**, on the server too. `stories_dir`,
  `bibles_dir`, `config.yaml` and the ledger all resolve against the working
  directory — see the [root README](../../README.md).

---

Related: [the review UI](review-ui.md) · [running the pipeline](running.md) ·
[setup](setup.md)
