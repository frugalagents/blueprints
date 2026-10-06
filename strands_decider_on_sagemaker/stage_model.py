"""Package decider + base weights into model.tar.gz and upload to S3.

Layout inside the archive (extracted to /opt/ml/model on the endpoint):
    decider/   checkpoint, with hobson_config.json base_model -> /opt/ml/model/base
    base/      Qwen/Qwen3.5-2B-Base
"""
import json
import os
import shutil
import sys
import tarfile

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
SKIP = {".cache", ".gitattributes"}
STAGE = "build/stage"
ARCHIVE = "build/model.tar.gz"


def main() -> None:
    sts = boto3.client("sts", region_name=REGION)
    bucket = f"sagemaker-{REGION}-{sts.get_caller_identity()['Account']}"
    key = "strands-decider/model/model.tar.gz"

    shutil.rmtree(STAGE, ignore_errors=True)
    os.makedirs(STAGE)
    for name in ("decider", "base"):
        shutil.copytree(f"model/{name}", f"{STAGE}/{name}", ignore=lambda d, fs: [f for f in fs if f in SKIP])

    cfg_path = f"{STAGE}/decider/hobson_config.json"
    cfg = json.load(open(cfg_path))
    cfg["base_model"] = "/opt/ml/model/base"
    json.dump(cfg, open(cfg_path, "w"), indent=2)

    print("creating archive (gzip level 1)...")
    with tarfile.open(ARCHIVE, "w:gz", compresslevel=1) as tar:
        for name in ("decider", "base"):
            tar.add(f"{STAGE}/{name}", arcname=name)
    print(f"archive: {os.path.getsize(ARCHIVE) / 1e9:.2f} GB")

    s3 = boto3.client("s3", region_name=REGION)
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        print("creating bucket", bucket)
        s3.create_bucket(Bucket=bucket)
    print(f"uploading to s3://{bucket}/{key} ...")
    s3.upload_file(ARCHIVE, bucket, key)
    uri = f"s3://{bucket}/{key}"
    open("build/model_uri.txt", "w").write(uri)
    print("done:", uri)


if __name__ == "__main__":
    sys.exit(main())
