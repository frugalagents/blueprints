"""Deploy the Strands Decider real-time endpoint with the SageMaker SDK.

Prereqs: python stage_model.py && python build_image.py   (writes build/model_uri.txt, build/image_uri.txt)
Usage:   python deploy.py
"""
import json
import os
import time
import warnings

import boto3

warnings.filterwarnings("ignore")

REGION = os.environ.get("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
ENDPOINT_NAME = os.environ.get("ENDPOINT_NAME", "strands-decider")
INSTANCE_TYPE = os.environ.get("INSTANCE_TYPE", "ml.g5.xlarge")
ROLE_NAME = "strands-decider-sagemaker-exec"


def ensure_role(bucket: str) -> str:
    iam = boto3.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [{"Effect": "Allow", "Principal": {"Service": "sagemaker.amazonaws.com"}, "Action": "sts:AssumeRole"}],
    }
    try:
        arn = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
        created = True
    except iam.exceptions.EntityAlreadyExistsException:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        created = False
    account = arn.split(":")[4]
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": f"arn:aws:ecr:{REGION}:{account}:repository/strands-decider",
            },
            {"Effect": "Allow", "Action": "s3:GetObject", "Resource": f"arn:aws:s3:::{bucket}/strands-decider/*"},
            {"Effect": "Allow", "Action": "s3:ListBucket", "Resource": f"arn:aws:s3:::{bucket}"},
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
                "Resource": "*",
            },
            {"Effect": "Allow", "Action": "cloudwatch:PutMetricData", "Resource": "*"},
        ],
    }
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="endpoint", PolicyDocument=json.dumps(policy))
    if created:
        print("created role", ROLE_NAME)
        time.sleep(15)  # IAM propagation
    return arn


def main() -> None:
    from sagemaker.serve import ModelBuilder

    image_uri = open("build/image_uri.txt").read().strip()
    model_uri = open("build/model_uri.txt").read().strip()
    bucket = model_uri.split("/")[2]
    role_arn = ensure_role(bucket)

    sm = boto3.client("sagemaker", region_name=REGION)
    try:
        sm.describe_endpoint(EndpointName=ENDPOINT_NAME)
        exists = True
    except sm.exceptions.ClientError:
        exists = False

    print(f"image: {image_uri}\nmodel: {model_uri}\ninstance: {INSTANCE_TYPE}\nendpoint: {ENDPOINT_NAME} (update={exists})")
    builder = ModelBuilder(
        image_uri=image_uri,
        s3_model_data_url=model_uri,
        role_arn=role_arn,
        instance_type=INSTANCE_TYPE,
        env_vars={"DECIDER_MODEL_NAME": "strands-decider-2B-hobson-v19"},
    )
    builder.build(model_name=f"{ENDPOINT_NAME}-{time.strftime('%Y%m%d-%H%M%S')}")
    builder.deploy(
        endpoint_name=ENDPOINT_NAME,
        initial_instance_count=1,
        instance_type=INSTANCE_TYPE,
        container_timeout_in_seconds=900,
        # Default GPU host image has a CUDA 11.4 driver; torch cu126 needs a 12.x driver.
        inference_ami_version="al2-ami-sagemaker-inference-gpu-3-1",
        update_endpoint=exists,
        wait=True,
    )
    d = sm.describe_endpoint(EndpointName=ENDPOINT_NAME)
    print("endpoint status:", d["EndpointStatus"])
    print(f"\nTest it:  python test_endpoint.py --endpoint {ENDPOINT_NAME}")
    print(f"Tear down: python destroy.py --endpoint {ENDPOINT_NAME}")


if __name__ == "__main__":
    main()
