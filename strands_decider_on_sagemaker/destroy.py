"""Delete the endpoint, its config(s) and model(s) so billing stops.

Usage: python destroy.py [--endpoint strands-decider]
"""
import argparse
import os

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
ap = argparse.ArgumentParser()
ap.add_argument("--endpoint", default="strands-decider")
name = ap.parse_args().endpoint

sm = boto3.client("sagemaker", region_name=REGION)
try:
    cfg = sm.describe_endpoint(EndpointName=name)["EndpointConfigName"]
except sm.exceptions.ClientError:
    cfg = None
    print("endpoint not found:", name)

if cfg:
    models = [v["ModelName"] for v in sm.describe_endpoint_config(EndpointConfigName=cfg)["ProductionVariants"]]
    sm.delete_endpoint(EndpointName=name)
    print("deleting endpoint", name)
    sm.get_waiter("endpoint_deleted").wait(EndpointName=name)
    sm.delete_endpoint_config(EndpointConfigName=cfg)
    for m in models:
        sm.delete_model(ModelName=m)
    print("deleted endpoint, config", cfg, "and models", models)
