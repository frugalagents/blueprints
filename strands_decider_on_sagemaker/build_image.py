"""Build and push the serving image with CodeBuild (no local Docker needed).

Idempotent: creates the ECR repo, CodeBuild role and project if missing.
Usage: python build_image.py [tag]
"""
import io
import json
import os
import sys
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-east-1")
REPO = "strands-decider"
PROJECT = "strands-decider-image-build"
ROLE = "strands-decider-codebuild"
TAG = sys.argv[1] if len(sys.argv) > 1 else "v1"

sts = boto3.client("sts", region_name=REGION)
iam = boto3.client("iam")
ecr = boto3.client("ecr", region_name=REGION)
cb = boto3.client("codebuild", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
logs = boto3.client("logs", region_name=REGION)

ACCOUNT = sts.get_caller_identity()["Account"]
BUCKET = f"sagemaker-{REGION}-{ACCOUNT}"
CONTEXT_KEY = "strands-decider/build/context.zip"
IMAGE_URI = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{REPO}:{TAG}"

BUILDSPEC = """version: 0.2
phases:
  pre_build:
    commands:
      - aws ecr get-login-password --region $AWS_DEFAULT_REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$AWS_DEFAULT_REGION.amazonaws.com
  build:
    commands:
      - docker build -t $IMAGE_URI .
  post_build:
    commands:
      - docker push $IMAGE_URI
"""


def ensure_bucket() -> None:
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)


def ensure_repo() -> None:
    try:
        ecr.create_repository(repositoryName=REPO)
        print("created ECR repo", REPO)
    except ecr.exceptions.RepositoryAlreadyExistsException:
        pass


def ensure_role() -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "codebuild.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }
    try:
        arn = iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        print("created role", ROLE)
        created = True
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=ROLE)["Role"]["Arn"]
        created = False
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
                ],
                "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/{REPO}",
            },
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], "Resource": "*"},
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{BUCKET}/strands-decider/build/*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE, PolicyName="build", PolicyDocument=json.dumps(policy))
    if created:
        time.sleep(15)  # IAM propagation
    return arn


def ensure_project(role_arn: str) -> None:
    spec = dict(
        name=PROJECT,
        source={"type": "S3", "location": f"{BUCKET}/{CONTEXT_KEY}", "buildspec": BUILDSPEC},
        artifacts={"type": "NO_ARTIFACTS"},
        environment={
            "type": "LINUX_CONTAINER",
            "image": "aws/codebuild/standard:7.0",
            "computeType": "BUILD_GENERAL1_MEDIUM",
            "privilegedMode": True,
        },
        serviceRole=role_arn,
    )
    for attempt in range(6):
        try:
            try:
                cb.create_project(**spec)
                print("created CodeBuild project", PROJECT)
            except cb.exceptions.ResourceAlreadyExistsException:
                cb.update_project(**spec)
            return
        except cb.exceptions.InvalidInputException as e:
            if "assume" in str(e).lower() and attempt < 5:
                time.sleep(10)
                continue
            raise


def upload_context() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("Dockerfile", "app.py", "serve"):
            z.write(f"container/{name}", name)
    s3.put_object(Bucket=BUCKET, Key=CONTEXT_KEY, Body=buf.getvalue())


def main() -> int:
    ensure_bucket()
    ensure_repo()
    ensure_project(ensure_role())
    upload_context()
    build = cb.start_build(
        projectName=PROJECT,
        environmentVariablesOverride=[
            {"name": "IMAGE_URI", "value": IMAGE_URI, "type": "PLAINTEXT"},
            {"name": "ACCOUNT", "value": ACCOUNT, "type": "PLAINTEXT"},
        ],
    )["build"]
    bid = build["id"]
    print("started build", bid)
    last = None
    while True:
        b = cb.batch_get_builds(ids=[bid])["builds"][0]
        phase = b.get("currentPhase")
        if phase != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {phase} ({b['buildStatus']})")
            last = phase
        if b["buildStatus"] != "IN_PROGRESS":
            break
        time.sleep(15)
    if b["buildStatus"] != "SUCCEEDED":
        print("BUILD FAILED:", b["buildStatus"])
        lg = b.get("logs", {})
        if lg.get("groupName"):
            ev = logs.get_log_events(logGroupName=lg["groupName"], logStreamName=lg["streamName"], limit=60, startFromHead=False)
            print("".join(e["message"] for e in ev["events"]))
        return 1
    os.makedirs("build", exist_ok=True)
    open("build/image_uri.txt", "w").write(IMAGE_URI)
    print("image:", IMAGE_URI)
    return 0


if __name__ == "__main__":
    sys.exit(main())
