# CDK TypeScript project - STNO - north-south routing with inspection firewall components - ServiceCatalog Product

This is an automation project using CDK development with TypeScript.

## Purpose

Setup Service Catalog Portfolio, Product for provisioning of North  -  South Routing with Inspection VPC comprising of AWS NFW in Shared Network Account
- Creates Service Catalog Portfolio
- Creates Service Catalog Product

## Setup

- Access to this Service Catalog Portfolio is granted to IAM User Group: **sc-northsouthfw-endusers**
- By default scenduser IAM user is added to **sc-northsouthfw-endusers** *IAM User Group*

> To change default IAM user `scenduser`, CDK app needs to be redeployed
> - Change the IAM user name in `cdk.json`
> Default S3 Bucket: `sh-network-dev-bucket1`

## File Upload
- src\northsouthfw_provider.yaml
- src\northsouthfw_provider.zip

## Provisioning Service Catalog Product
- Login to Shared Network Account as **scenduser**
- Select **North-South Firewall Routing** from Products page
- Launch Product from *Service Catalog Console*
- Provide Parameters:
  - Member Account Id
  - Member Account Region
  - Member VPC Id
  - Member VPC CIDR
  - Hub Internal VPC Id
  - Hub Inspection VPC Id
  - Hub Egress VPC Id

### Result

- Adds Routes for egress traffic from Member VPC pass through Inspection VPC NFW
- Adds Routes for ingress traffic from Internal VPC pass through Inspection VPC NFW

## Deprovisioning Service Catalog Product

- Terminate the provisioned product

### Result:

- Egress traffic routing is removed
- Ingress traffic routing is removed

## Useful commands

* `npm install`     download and install node module dependencies
* `npm run build`   compile typescript to js
* `npm run watch`   watch for changes and compile
* `npm run test`    perform the jest unit tests
* `cdk deploy`      deploy this stack to your default AWS account/region
* `cdk diff`        compare deployed stack with current state
* `cdk synth`       emits the synthesized CloudFormation template

