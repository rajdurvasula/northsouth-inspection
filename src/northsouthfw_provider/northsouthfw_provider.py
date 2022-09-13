from compileall import compile_dir
import os
import sys
import json
import boto3
import urllib3
from datetime import date, datetime
import time
import logging

LOGGER = logging.getLogger()
if 'log_level' in os.environ:
    LOGGER.setLevel(os.environ['log_level'])
    print('Log level set to %s' % LOGGER.getEffectiveLevel())
else:
    LOGGER.setLevel(logging.ERROR)

session = boto3.Session()

# environment variables
# EGRESS_TGW_ROUTETABLE = 'Egress-RTB'
# FLAT_TGW_ROUTETABLE = "Flat"
# INSPECTION_TGW_ROUTETABLE = "Inspection-RTB"

# variables
anywhere_cidr = '0.0.0.0/0'

def json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError('Type %s not serializable' % type(obj))

def send_response(event, context, responseStatus, responseData, physicalResourceId=None,noEcho=False):
    responseUrl = event['ResponseURL']
    ls = context.log_stream_name
    responseBody = {}
    responseBody['Status'] = responseStatus
    responseBody['Reason'] = 'View details in Log Stream: '+ls
    responseBody['PhysicalResourceId'] = physicalResourceId or ls
    responseBody['StackId'] = event['StackId']
    responseBody['RequestId'] = event['RequestId']
    responseBody['LogicalResourceId'] = event['LogicalResourceId']
    responseBody['NoEcho'] = noEcho
    responseBody['Data'] = responseData
    jsonResponseBody = json.dumps(responseBody)
    print('ResponseBody: \n'+jsonResponseBody)
    headers = {
        'content-type': '',
        'content-length': str(len(jsonResponseBody))
    }
    http = urllib3.PoolManager()
    try:
        response = http.request('PUT', responseUrl, body=jsonResponseBody, headers=headers)
        print('StatusCode = '+response.reason)
    except Exception as e:
        print(f'send_response(..) failed executing requests.put(..): {e}')

def get_vpc_name(ec2_client, vpc_id):
    filters = []
    vpcIdFilter = {
        'Name': 'vpc-id',
        'Values': [ vpc_id ]
    }
    filters.append(vpcIdFilter)
    vpc_name = ''
    try:
        response = ec2_client.describe_vpcs(Filters=filters)
        if len(response['Vpcs']) == 1:
            for tag in response['Vpcs'][0]['Tags']:
                if tag['Key'] == 'Name':
                    vpc_name = tag['Value']
                    break
        else:
            raise ValueError('VPC Id: {} not found'.format(vpc_id))
        return vpc_name
    except Exception as e:
        print(f'failed in describe_vpcs(..): {e}')
        print(str(e))
        raise e

def get_subnets(ec2_client, vpc_id):
    filters = []
    vpcFilter = {
        'Name': 'vpc-id',
        'Values': [ vpc_id ]
    }
    filters.append(vpcFilter)
    subnet_list = []
    try:
        response = ec2_client.describe_subnets(Filters=filters)
        for subnet in response['Subnets']:
            subnet_map = {
                'SubnetId': subnet['SubnetId'],
                'AvailabilityZone': subnet['AvailabilityZone'],
                'CidrBlock': subnet['CidrBlock']
            }
            for tag in subnet['Tags']:
                if tag['Key'] == 'Purpose':
                    subnet_map.update({'Purpose': tag['Value']})
            subnet_list.append(subnet_map)
        print('Return Subnets ..')
        print(subnet_list)
        return subnet_list
    except Exception as e:
        print(f'failed in describe_subnets(..): {e}')
        print(str(e))
        raise e

def get_rtbs(ec2_client, vpc_id):
    filters = []
    vpcFilter = {
        'Name': 'vpc-id',
        'Values': [ vpc_id ]
    }
    mainFilter = {
        'Name': 'association.main',
        'Values': [ 'false' ]
    }
    filters.append(vpcFilter)
    filters.append(mainFilter)
    rtb_list = []
    try:
        response = ec2_client.describe_route_tables(Filters=filters)
        for rtb in response['RouteTables']:
            subnet_id = ''
            for association in rtb['Associations']:
                subnet_id = association['SubnetId']
            rtb_map = {
                'RouteTableId': rtb['RouteTableId'],
                'SubnetId': subnet_id
            }
            for tag in rtb['Tags']:
                if tag['Key'] == 'Purpose':
                    rtb_map.update({ 'Purpose': tag['Value'] })
            rtb_list.append(rtb_map)
        print('Return RouteTables ..')
        print(rtb_list)
        return rtb_list
    except Exception as e:
        print(f'failed in describe_route_tables(..): {e}')
        print(str(e))
        raise e

def create_tgw_attach(ec2_client, vpc_id, subnet_list, tgw_id):
    try:
        vpc_name = get_vpc_name(ec2_client, vpc_id)
        tgw_attach_name = '{}-attach'.format(vpc_name)
        tag_specs = []
        tag_spec = {
            'ResourceType': 'transit-gateway-attachment',
            'Tags': [
                {
                    'Key': 'Name',
                    'Value': tgw_attach_name
                }
            ]
        }
        tag_specs.append(tag_spec)        
        # get tgw subnets
        tgw_subnet_list = []
        tgw_subnet_ids = []
        for subnet_map in subnet_list:
            if subnet_map['Purpose'] == 'tgw':
                tgw_subnet_list.append(subnet_map)
                tgw_subnet_ids.append(subnet_map['SubnetId'])
        response = ec2_client.create_transit_gateway_vpc_attachment(
            TransitGatewayId=tgw_id,
            VpcId=vpc_id,
            SubnetIds=tgw_subnet_ids,
            TagSpecifications=tag_specs
        )
        tgw_attach_id = response['TransitGatewayVpcAttachment']['TransitGatewayAttachmentId']
        print('Created TGW Attachment with Id: {} for VPC Id: {}'.format(tgw_attach_id, vpc_id))
        inspect_tgw_attach_subnets = {
            'SubnetList': tgw_subnet_list,
            'TGWAttachmentId': tgw_attach_id,
            'TGWAttachmentName': tgw_attach_name
        }
        return inspect_tgw_attach_subnets
    except Exception as e:
        print(f'failed in create_transit_gateway_vpc_attachment(..): {e}')
        print(str(e))
        raise e

def is_tgw_attachment_available(ec2_client, tgw_attach_id):
    tgw_attachment_available = False
    filters = []
    idFilter = {
        'Name': 'transit-gateway-attachment-id',
        'Values': [ tgw_attach_id ]
    }
    filters.append(idFilter)
    wait_count = int(10)
    wait_interval = int(60)
    try:
        while (wait_count > 0):
            response = ec2_client.describe_transit_gateway_vpc_attachments(Filters=filters)
            if response['TransitGatewayVpcAttachments'][0]['State'] == 'available':
                tgw_attachment_available = True
                break
            else:
                print('Wait for {} seconds'.format(str(wait_interval)))
                time.sleep(wait_interval)
            wait_count = wait_count - 1
    except Exception as e:
        print(f'failed in describe_transit_gateway_vpc_attachments(..): {e}')
        print(str(e))
        raise e
    return tgw_attachment_available

def delete_tgw_attach(ec2_client, vpc_id):
    try:
        tgw_attach = get_tgw_attachment(ec2_client, vpc_id)
        ec2_client.delete_transit_gateway_vpc_attachment(
            TransitGatewayAttachmentId=tgw_attach
        )
        print('Deleted TGW VPC Attachment: {} for VPC Id: {}'.format(tgw_attach, vpc_id))
    except Exception as e:
        print(f'failed in delete_transit_gateway_vpc_attachment(..): {e}')
        print(str(e))
        raise e

def add_routes(ec2_client, cidr_block, vpc_id, tgw_id, purpose_flag):
    try:
        rtb_list = get_rtbs(ec2_client, vpc_id)
        for rtb_map in rtb_list:
            if rtb_map['Purpose'] == purpose_flag:
                rtb_id = rtb_map['RouteTableId']
                ec2_client.create_route(
                    DestinationCidrBlock=cidr_block,
                    TransitGatewayId=tgw_id,
                    RouteTableId=rtb_id
                )
                print('Added Route for CIDR: {} in Route Table: {}'.format(cidr_block, rtb_id))
    except Exception as e:
        print(f'failed in create_route(..): {e}')
        print(str(e))
        raise e

def delete_routes(ec2_client, cidr_block, vpc_id, purpose_flag):
    try:
        rtb_list = get_rtbs(ec2_client, vpc_id)
        for rtb_map in rtb_list:
            if rtb_map['Purpose'] == purpose_flag:
                rtb_id = rtb_map['RouteTableId']
                ec2_client.delete_route(
                    DestinationCidrBlock=cidr_block,
                    RouteTableId=rtb_id
                )
                print('Deleted Route for CIDR: {} in Route Table: {}'.format(cidr_block, rtb_id))
    except Exception as e:
        print(f'failed in delete_route(..): {e}')
        print(str(e))
        raise e

def get_tgw_rtb_id(ec2_client, tgw_rtb_name):
    filters = []
    nameFilter = {
        'Name': 'tag:Name',
        'Values': [ tgw_rtb_name ]
    }
    filters.append(nameFilter)
    try:
        response = ec2_client.describe_transit_gateway_route_tables(
            Filters=filters
        )
        flat_tgw_rtb_id = ''
        if len(response['TransitGatewayRouteTables']) == 1:
            flat_tgw_rtb_id = response['TransitGatewayRouteTables'][0]['TransitGatewayRouteTableId']
        else:
            raise ValueError('Flat TGW Route Table does not exist')
        return flat_tgw_rtb_id
    except Exception as e:
        print(f'failed in describe_transit_gateway_route_tables(..): {e}')
        print(str(e))
        raise e

def associate_to_flat(ec2_client, vpc_attach_id):
    try:
        flat_tgw_rtb_id = get_tgw_rtb_id(ec2_client, os.environ['FLAT_TGW_ROUTETABLE'])
        response = ec2_client.associate_transit_gateway_route_table(
            TransitGatewayRouteTableId=flat_tgw_rtb_id,
            TransitGatewayAttachmentId=vpc_attach_id
        )
        print('TGW VPC Attachment: {} associated with TGW Route Table: {}'.format(vpc_attach_id, flat_tgw_rtb_id))
    except Exception as e:
        print(f'failed in associate_transit_gateway_route_table(..): {e}')
        print(str(e))

def add_tgw_route(ec2_client, cidr_block, tgw_attach_id, tgw_rtb_name):
    try:
        tgw_rtb_id = get_tgw_rtb_id(ec2_client, tgw_rtb_name)
        ec2_client.create_transit_gateway_route(
            DestinationCidrBlock=cidr_block,
            TransitGatewayRouteTableId=tgw_rtb_id,
            TransitGatewayAttachmentId=tgw_attach_id
        )
        print('Added TGW Route for CIDR: {} to TGW Route Table: {}'.format(cidr_block, tgw_rtb_name))
    except Exception as e:
        print(f'failed in create_transit_gateway_route(..): {e}')
        print(str(e))
        raise e

def delete_tgw_route(ec2_client, cidr_block, tgw_rtb_name):
    try:
        tgw_rtb_id = get_tgw_rtb_id(ec2_client, tgw_rtb_name)
        ec2_client.delete_transit_gateway_route(
            TransitGatewayRouteTableId=tgw_rtb_id,
            DestinationCidrBlock=cidr_block
        )
        print('Deleted TGW Route for CIDR: {} from TGW Route Table: {}'.format(cidr_block, tgw_rtb_name))
    except Exception as e:
        print(f'failed in delete_transit_gateway_route(..): {e}')
        print(str(e))
        raise e

def get_tgw_attachment(ec2_client, vpc_id):
    filters = []
    vpcFilter = {
        'Name': 'resource-id',
        'Values': [ vpc_id ]
    }
    stateFilter = {
        'Name': 'state',
        'Values': [ 'available' ]
    }
    filters.append(vpcFilter)
    filters.append(stateFilter)
    tgw_attach_id = ''
    try:
        response = ec2_client.describe_transit_gateway_attachments(
            Filters=filters
        )
        tgw_attach_name = ''
        if len(response['TransitGatewayAttachments']) == 1:
            tgw_attach = response['TransitGatewayAttachments'][0]
            tgw_attach_id = tgw_attach['TransitGatewayAttachmentId']
            print('Return TGW VPC Attachment Id: {}'.format(tgw_attach_id))
            return tgw_attach_id
        else:
            raise ValueError('TGW VPC Attachment not found')
    except Exception as e:
        print(f'failed in describe_transit_gateway_attachments(..): {e}')
        print(str(e))
        raise e

def associate_to_inspect(ec2_client, vpc_id):
    try:
        tgw_attach_id = get_tgw_attachment(ec2_client, vpc_id)
        tgw_rtb_id = get_tgw_rtb_id(ec2_client, os.environ['INSPECTION_TGW_ROUTETABLE'])
        ec2_client.associate_transit_gateway_route_table(
            TransitGatewayRouteTableId=tgw_rtb_id,
            TransitGatewayAttachmentId=tgw_attach_id
        )
        print('TGW VPC Attachment: {} associated to TGW Route Table: {}'.format(tgw_attach_id, tgw_rtb_id))
    except Exception as e:
        print(f'failed in associate_transit_gateway_route_table(..): {e}')
        print(str(e))
        raise e

def is_association_deleted(ec2_client, tgw_attach_id):
    tgw_association_deleted = False
    filters = []
    idFilter = {
        'Name': 'transit-gateway-attachment-id',
        'Values': [ tgw_attach_id ]
    }
    filters.append(idFilter)
    wait_count = int(10)
    wait_interval = int(60)
    try:
        while (wait_count > 0):
            response = ec2_client.describe_transit_gateway_attachments(Filters=filters)
            if 'Association' not in response['TransitGatewayAttachments'][0].keys():
                tgw_association_deleted = True
                break
            else:
                print('Wait for {} seconds'.format(str(wait_interval)))
                time.sleep(wait_interval)
            wait_count = wait_count - 1
    except Exception as e:
        print(f'failed in describe_transit_gateway_attachments(..): {e}')
        print(str(e))
        raise e
    return tgw_association_deleted

def disassociate_from_flat(ec2_client, vpc_id):
    try:
        tgw_attach_id = get_tgw_attachment(ec2_client, vpc_id)
        flat_tgw_rtb_id = get_tgw_rtb_id(ec2_client, os.environ['FLAT_TGW_ROUTETABLE'])
        ec2_client.disassociate_transit_gateway_route_table(
            TransitGatewayRouteTableId=flat_tgw_rtb_id,
            TransitGatewayAttachmentId=tgw_attach_id
        )
        print('Disassociated TGW VPC Attachment: {} from TGW Route Table: {}'.format(tgw_attach_id, flat_tgw_rtb_id))
    except Exception as e:
        print(f'failed in disassociate_transit_gateway_route_table(..): {e}')
        print(str(e))
        raise e

def disassociate_from_inspect(ec2_client, vpc_id):
    try:
        tgw_attach_id = get_tgw_attachment(ec2_client, vpc_id)
        inspect_tgw_rtb_id = get_tgw_rtb_id(ec2_client, os.environ['INSPECTION_TGW_ROUTETABLE'])
        ec2_client.disassociate_transit_gateway_route_table(
            TransitGatewayRouteTableId=inspect_tgw_rtb_id,
            TransitGatewayAttachmentId=tgw_attach_id
        )
        print('Disassociated TGW VPC Attachment: {} from TGW Route Table: {}'.format(tgw_attach_id, inspect_tgw_rtb_id))
    except Exception as e:
        print(f'failed in disassociate_transit_gateway_route_table(..): {e}')
        print(str(e))
        raise e

def create_operation(event, context):
    try:
        resProps = event['ResourceProperties']
        member_account = resProps['member_account']
        member_region = resProps['member_region']
        member_vpc_id = resProps['member_vpc_id']
        member_cidr = resProps['member_cidr']
        hub_intern_vpc_id = resProps['hub_intern_vpc_id']
        hub_inspect_vpc_id = resProps['hub_inspect_vpc_id']
        hub_egress_vpc_id = resProps['hub_egress_vpc_id']
        tgw_id = resProps['tgw_id']
        ec2_client = session.client('ec2')
        # Get Inspection VPC subnets
        #inspect_subnet_list = get_subnets(ec2_client, hub_inspect_vpc_id)
        # Create Inspection TGW VPC Attachment
        inspect_tgw_attach_id = get_tgw_attachment(ec2_client, hub_inspect_vpc_id)
        # Disassociate Member VPC from Flat TGW RTB
        # Member VPC is associated to Flat as part of STNO process
        disassociate_from_flat(ec2_client, member_vpc_id)
        # Add routes to Inspection VPC FW RTBs
        if is_tgw_attachment_available(ec2_client, inspect_tgw_attach_id):
            add_routes(ec2_client, anywhere_cidr, hub_inspect_vpc_id, tgw_id, 'firewall')
            # Associate Inspection VPC Attachment to Flat TGW RTB
            associate_to_flat(ec2_client, inspect_tgw_attach_id)
        # Associate Member VPC from Inspection TGW RTB
        member_tgw_attach_id = get_tgw_attachment(ec2_client, member_vpc_id)
        if is_association_deleted(ec2_client, member_tgw_attach_id):
            associate_to_inspect(ec2_client, member_vpc_id)
        # Associate Internal VPC TGW Attachment to Inspection TGW RTB
        associate_to_inspect(ec2_client, hub_intern_vpc_id)
        # Add TGW Route for Inspection VPC Attachment to Inspection TGW RTB
        add_tgw_route(ec2_client, anywhere_cidr, inspect_tgw_attach_id, os.environ['INSPECTION_TGW_ROUTETABLE'])
        # Associate Egress VPC Attachment to Flat TGW RTB
        egress_tgw_attach_id = get_tgw_attachment(ec2_client, hub_egress_vpc_id)
        associate_to_flat(ec2_client, egress_tgw_attach_id)
        # Add TGW Route for Egress VPC Attachment to Flat TGW RTB
        add_tgw_route(ec2_client, anywhere_cidr, egress_tgw_attach_id, os.environ['FLAT_TGW_ROUTETABLE'])
        # Add Route to TGW Route Tables in Egress VPC Route Tables for Member VPC
        add_routes(ec2_client, member_cidr, hub_egress_vpc_id, tgw_id, 'tgw')
        # Add Route to Public Route Tables in Egress VPC for Member VPC
        add_routes(ec2_client, member_cidr, hub_egress_vpc_id, tgw_id, 'public')
        # Add Route to TGW in Internal VPC TGW Route Tables for Member VPC
        add_routes(ec2_client, member_cidr, hub_intern_vpc_id, tgw_id, 'tgw')
        # Add Route to Public Route Tables in Internal VPC for Member VPC
        add_routes(ec2_client, member_cidr, hub_intern_vpc_id, tgw_id, 'public')
        # Add Route for Member VPC CIDR to Egress TGW RTB
        add_tgw_route(ec2_client, member_cidr, egress_tgw_attach_id, os.environ['EGRESS_TGW_ROUTETABLE'])
        responseData = {
            'InspectionVpcId': hub_inspect_vpc_id,
            'InspectionTGWVpcAttachment': inspect_tgw_attach_id,
            'MemberTGWVpcAttachment': member_tgw_attach_id,
            'Status': 'Ok'
        }
        send_response(event, context, 'SUCCESS', responseData)
    except Exception as e:
        print(f'failed in create_operation(..): {e}')
        print(str(e))
        responseData = {
            'exc_info': str(e)
        }
        send_response(event, context, 'FAILED', responseData)

def delete_operation(event, context):
    try:
        resProps = event['ResourceProperties']
        member_account = resProps['member_account']
        member_region = resProps['member_region']
        member_vpc_id = resProps['member_vpc_id']
        member_cidr = resProps['member_cidr']
        hub_intern_vpc_id = resProps['hub_intern_vpc_id']
        hub_inspect_vpc_id = resProps['hub_inspect_vpc_id']
        hub_egress_vpc_id = resProps['hub_egress_vpc_id']
        tgw_id = resProps['tgw_id']
        ec2_client = session.client('ec2')
        # Delete TGW Route for Member VPC CIDR from Egress TGW Route Table
        delete_tgw_route(ec2_client, member_cidr, os.environ['EGRESS_TGW_ROUTETABLE'])
        # Delete Route to Public Route Tables in Internal VPC for Member VPC
        delete_routes(ec2_client, member_cidr, hub_intern_vpc_id, 'public')
        # Delete Route to TGW in Internal VPC Route Tables
        delete_routes(ec2_client, member_cidr, hub_intern_vpc_id, 'tgw')
        # Delete Route to Public in Egress VPC Route Tables
        delete_routes(ec2_client, member_cidr, hub_egress_vpc_id, 'public')
        # Delete Route to TGW in Egress VPC Route Tables
        delete_routes(ec2_client, member_cidr, hub_egress_vpc_id, 'tgw')
        # Delete TGW Route for Egress VPC Attachment from Flat TGW Route Table
        delete_tgw_route(ec2_client, anywhere_cidr, os.environ['FLAT_TGW_ROUTETABLE'])
        # Disassociate Egress VPC Attachment to Flat TGW RTB
        disassociate_from_flat(ec2_client, hub_egress_vpc_id)
        # Delete TGW Route for Inspection VPC Attachment to Inspection TGW Route Table
        delete_tgw_route(ec2_client, anywhere_cidr, os.environ['INSPECTION_TGW_ROUTETABLE'])
        # Disassociate Internal VPC TGW Attachment from Inspection TGW RTB
        disassociate_from_inspect(ec2_client, hub_intern_vpc_id)
        # Disassociate Member VPC TGW Attachment from Inspection TGW RTB
        disassociate_from_inspect(ec2_client, member_vpc_id)
        # Disassociate Inspection VPC Attachment to Flat TGW RTB
        disassociate_from_flat(ec2_client, hub_inspect_vpc_id)
        # Delete routes from Inspection VPC FW RTBs
        delete_routes(ec2_client, anywhere_cidr, hub_inspect_vpc_id, 'firewall')
        # Delete Inspection TGW VPC Attachment
        responseData = {
            'InspectionVpcId': hub_inspect_vpc_id,
            'Status': 'Ok'
        }
        send_response(event, context, 'SUCCESS', responseData)
    except Exception as e:
        print(f'failed in delete_operation(..): {e}')
        print(str(e))
        responseData = {
            'exc_info': str(e)
        }
        send_response(event, context, 'FAILED', responseData)        

def lambda_handler(event, context):
    print(f"REQUEST RECEIVED: {json.dumps(event, default=str)}")
    responseData = {}
    if 'RequestType' in event:
        if event['RequestType'] == 'Create':
            create_operation(event, context)
        elif event['RequestType'] == 'Delete':
            delete_operation(event, context)
        else:
            send_response(event, context, 'SUCCESS', responseData)

# standalone test
#def main():
#    os.environ['EGRESS_TGW_ROUTETABLE'] = 'Egress-RTB'
#    os.environ['FLAT_TGW_ROUTETABLE'] = 'Flat'
#    os.environ['INSPECTION_TGW_ROUTETABLE'] = 'Inspection-RTB'
#    event = {
#        'RequestType': 'Delete',
#        'ResourceProperties': {
#            'member_account': '172489758104',
#            'member_region': 'us-east-1',
#            'member_vpc_id': 'vpc-0d050dac9430169ee',
#            'member_cidr': '10.224.0.0/24',
#            'hub_intern_vpc_id': 'vpc-01816696b8aaad08d',
#            'hub_inspect_vpc_id': 'vpc-0d81e3be919b5f589',
#            'hub_egress_vpc_id': 'vpc-0d79480e2deb0e821',
#            'tgw_id': 'tgw-03810b18479dad03f'
#        }
#    }
#    context = {}
#    lambda_handler(event, context)
#
#if __name__ == '__main__':
#    main()
