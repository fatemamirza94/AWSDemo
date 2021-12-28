import sys
import os

from flask import Flask, flash, redirect, render_template, request, url_for, abort, Markup, session
from flask_bootstrap import Bootstrap 

from flask_wtf import FlaskForm
from wtforms import HiddenField, SelectField, SelectMultipleField, TextAreaField, StringField
from flask_wtf.file import FileField
from wtforms.validators import DataRequired

from werkzeug.utils import secure_filename

from time import time
from datetime import datetime, timedelta

import libcloud.security
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver

import boto3



class UploadForm(FlaskForm):
    file = FileField()


class CreateNodeForm(FlaskForm):

    name    = StringField(u'Name')
    image   = SelectField(u'AMI', coerce=str)
    size    = SelectField(u'Instance type', coerce=str)

    nodes_no        = StringField(u'Number of instances to start')
    key_pair        = SelectField(u'Key pair', coerce=str)
    security_group  = SelectField(u'Security group', coerce=str)
    subnet          = SelectField(u'Subnet', coerce=str)


class Subnet:
  def __init__(self, id):
    self.id = id


app = Flask(__name__)

app.secret_key = 'jamil'

Bootstrap(app)


def get_regions_list():
    ec2 = boto3.client('ec2')
    response_regions = ec2.describe_regions()

    regions_list = []
    for region in response_regions['Regions']:
        regions_list.append(region['RegionName'])

    return regions_list



def switch_regions(region_name):
    session['current_region'] = region_name


def get_current_region():
    if 'current_region' not in session:
        session['current_region'] = 'eu-north-1'
    return session['current_region']



app.jinja_env.globals.update(get_regions_list=get_regions_list)
app.jinja_env.globals.update(get_current_region=get_current_region)



def get_credentials():
    access_key = None
    access_id  = None

    with open('credentials.txt') as f:
        access_id = f.readline().strip('\n')
        access_key = f.readline().strip('\n')
        
    return access_id, access_key


images = None
sizes = None

def get_ec2_driver(region):
    libcloud.security.VERIFY_SSL_CERT = False
    cls = get_driver(Provider.EC2)
    access_id, access_key = get_credentials()
    driver = cls(access_id, access_key, region=region)
    return driver


def get_ec2_images_list():
    global images
    if images is None:
        driver = get_ec2_driver(get_current_region())
        images = driver.list_images(ex_filters={
            'name':[
            'amzn2-ami-kernel-5.10-hvm-2.0.20211103.1-x86_64-gp2', 'ubuntu/images/hvm-ssd/ubuntu-focal-20.04-amd64-server-20211021', 
            'Windows_Server-2019-English-Full-Base-2021.11.10', 'debian-10-amd64-20210329-591', 
            'suse-sles-15-sp2-v20201211-hvm-ssd-x86_64', 'RHEL_HA-8.4.0_HVM-20210504-x86_64-2-Hourly2-GP2']
            })
    return images


def get_ec2_sizes_list():
    global sizes
    if sizes is None:
        driver = get_ec2_driver(get_current_region())
        sizes = driver.list_sizes()
    return sizes



@app.route('/')
def dashboard():
    return render_template('index.html')



@app.route('/switch-region/<region>')
def change_region(region):

    switch_regions(region)

    # request.referer gives the previous URL or / if none
    return_url = request.referrer or '/'

    flash(Markup('Switched region to <b>{}</b>'.format(region)))

    return redirect(return_url)




@app.route('/storage/list-buckets/<region_name>', methods=['GET', 'POST'])
@app.route('/storage/list-buckets/')
def buckets_list(region_name=None):

    region_name = get_current_region()
    s3 = boto3.client('s3', region_name=get_current_region())
    response_buckets = s3.list_buckets()
    bucket_list = []

    for bucket in response_buckets['Buckets']:
        try:
            bucket_location = s3.get_bucket_location(Bucket=bucket['Name'])['LocationConstraint']
            if region_name.lower() == 'global' or region_name == bucket_location:
                bucket_list.append({'name': bucket['Name'], 'bucket_location': bucket_location})
        except:
            print('{} is a ghost'.format(bucket['Name']), file=sys.stdout)

    return render_template('buckets-list.html', buckets=bucket_list, region_name=region_name)



@app.route('/storage/create-bucket', methods=['GET', 'POST'])
def create_bucket():

    ec2 = boto3.client('ec2')
    if request.method == 'GET':
        
        region_list = get_regions_list()
        
        return render_template('create-bucket.html', regions=region_list)
    
    elif request.method == 'POST':

        bucket_name = request.form['bucket-name']
        region_name = request.form['region-name']

        switch_regions(region_name)
        local_client = boto3.client('s3', region_name=get_current_region())

        try:
            response = local_client.create_bucket(Bucket=bucket_name, CreateBucketConfiguration={'LocationConstraint': region_name})
            flash(Markup('Successfully created <b>{}</b> in region <b>{}</b>'.format(bucket_name, region_name)))
        except Exception as e:
            flash(Markup(e.args[0]))
   
        return redirect(url_for('buckets_list', region_name=get_current_region()))



@app.route('/storage/delete-bucket/<region_name>/<bucket_name>')
def delete_bucket(region_name, bucket_name):

    local_resource = boto3.resource('s3', region_name=get_current_region())


    # delete all objects in bucket first
    bucket = local_resource.Bucket(bucket_name)
    for key in bucket.objects.all():
        key.delete()
    bucket.delete()
    
    flash(Markup('Successfully deleted <b>{}</b>'.format(bucket_name)))

    return redirect(url_for('buckets_list', region_name=region_name))



@app.route('/storage/list-files/<bucket_name>', methods=['GET', 'POST'])
def bucket(bucket_name):
    form = UploadForm()
    region_name = request.args['region_name']

    local_client = boto3.client('s3', region_name=get_current_region())

    response = local_client.list_objects(Bucket=bucket_name)
    file_list = []
    if 'Contents' in response:
        for file in response['Contents']:
            file_list.append({'name': file['Key']})

    return render_template('bucket.html', files=file_list, bucket_name=bucket_name, form=form)




@app.route('/storage/upload-file/<bucket_name>', methods=['POST'])
def upload_file(bucket_name):
    local_client = boto3.client('s3', region_name=get_current_region())

    form = UploadForm()
    f = form.file.data
    filename = secure_filename(f.filename)

    t1 = time()

    response = local_client.upload_fileobj(f, bucket_name, filename)

    t2 = time()
    dt = t2 - t1

    flash(Markup('Successfully uploaded <b>{}</b> (<i>{} seconds</i>)'.format(filename, dt)))

    return redirect(url_for('bucket', bucket_name=bucket_name, region_name=get_current_region()))




@app.route('/storage/download/<bucket_name>/<file_name>')
def download_file(bucket_name, file_name):
    
    local_client = boto3.client('s3', region_name=get_current_region())

    t1 = time()

    response = local_client.download_file(bucket_name, file_name, file_name)

    t2 = time()

    dt = t2 - t1

    flash(Markup('Successfully downloaded <b>{}</b> (<i>{} seconds</i>)'.format(file_name, dt)))
    
    return redirect(url_for('bucket', bucket_name=bucket_name, region_name=get_current_region()))




@app.route('/storage/delete-file/<bucket_name>/<file_name>')
def delete_file(bucket_name, file_name):
    local_client = boto3.client('s3', region_name=get_current_region())

    t1 = time()

    response = local_client.delete_object(Bucket=bucket_name, Key=file_name)

    t2 = time()
    dt = t2 - t1

    flash(Markup('Successfully deleted <b>{}</b> (<i>{} seconds</i>)'.format(file_name, dt)))
    
    return redirect(url_for('bucket', bucket_name=bucket_name, region_name=get_current_region()))




@app.route('/compute/list-nodes')
def nodes_list():
    driver = get_ec2_driver(get_current_region())
    nodes = driver.list_nodes()

    return render_template('nodes-list.html', nodes = nodes)




# both display and handle form submit, then redirect to nodes_list
@app.route('/compute/create-node', methods=['GET', 'POST'])
def create_node():
    form =  CreateNodeForm()
    if request.method == 'GET':
        region = get_current_region()
        driver = get_ec2_driver(region)


        images_local                = get_ec2_images_list()
        form.image.choices          = [(image.id, image.name) for image in images_local]
        sizes_local                 = get_ec2_sizes_list()
        form.size.choices           = [(size.id, size.name) for size in sizes_local]
        form.key_pair.choices       = [(key_pair.name, key_pair.name) for key_pair in driver.list_key_pairs()]
        form.key_pair.choices       = [('None', 'None')] + form.key_pair.choices
        form.security_group.choices = [(sg.id, sg.name) for sg in driver.ex_get_security_groups()]
        form.security_group.choices = [('None', 'None')] + form.security_group.choices
        form.subnet.choices         = [(subnet.id, subnet.name+" | "+subnet.extra['zone']) for subnet in driver.ex_list_subnets()]
        form.subnet.choices         = [('None', 'None')] + form.subnet.choices

        return render_template('create-node.html', form=form)
        
    elif request.method == 'POST':
        region = get_current_region()
        driver = get_ec2_driver(region)

        name            = form.name.data
        image_id        = form.image.data
        size_id         = form.size.data
        nodes_no_str    = form.nodes_no.data
        key_pair        = form.key_pair.data
        security_group  = form.security_group.data
        subnet          = form.subnet.data


        try:
            nodes_no = int(nodes_no_str)
        except:
            flash(Markup('Please, provide a valid number of instances to create.'))
            return redirect(url_for('create_node'))


        if (security_group != 'None' and subnet == 'None'):
            flash(Markup('Please supply ex_security_group_ids combinated with ex_subnet'))
            return redirect(url_for('create_node'))


        if subnet is not None:
            for subnets in driver.ex_list_subnets():
                if subnets.name != None and subnets.name in subnet:
                    s = Subnet(subnets.id)

        

        size = [s for s in get_ec2_sizes_list() if s.id == size_id][0]
        image = [im for im in get_ec2_images_list() if im.id == image_id][0]

        driver = get_ec2_driver(get_current_region())

        if ((key_pair != 'None') and (security_group != 'None') and (subnet != 'None')):
            node = driver.create_node(name=name, image=image, size=size, ex_mincount=nodes_no, ex_maxcount=nodes_no, 
                ex_keyname=key_pair, ex_security_group_ids=[security_group], ex_subnet=s)

        elif (key_pair != 'None' and security_group == 'None' and subnet == 'None'):
            node = driver.create_node(name=name, image=image, size=size, ex_mincount=nodes_no, ex_maxcount=nodes_no, 
                ex_keyname=key_pair)

        elif (key_pair != 'None' and security_group == 'None' and subnet != 'None'):
            node = driver.create_node(name=name, image=image, size=size, ex_mincount=nodes_no, ex_maxcount=nodes_no, 
                ex_keyname=key_pair, ex_subnet=s)

        elif (key_pair == 'None' and security_group == 'None' and subnet != 'None'):
            node = driver.create_node(name=name, image=image, size=size, ex_mincount=nodes_no, ex_maxcount=nodes_no, 
                ex_subnet=s)
        
        else:
            node = driver.create_node(name=name, image=image, size=size, ex_mincount=nodes_no, ex_maxcount=nodes_no)


        flash(Markup('Successfully created <b>{}</b> instance(s) <b>{}</b>'.format(nodes_no, name)))

        if (nodes_no == 1):
            return redirect(url_for('node_details', node_id=node.id))
        else:
            return redirect(url_for('node_details', node_id=node[0].id))




# redirect to node_details
@app.route('/compute/start-node/<node_id>')
def start_node(node_id):

    driver = get_ec2_driver(get_current_region())
    node = driver.list_nodes(ex_node_ids=[node_id])[0]
    driver.ex_start_node(node)
    flash(Markup('Starting {}...'.format(node.name)))

    return redirect(url_for('node_details', node_id=node_id))



@app.route('/compute/stop-node/<node_id>')
def stop_node(node_id):

    driver = get_ec2_driver(get_current_region())
    node = driver.list_nodes(ex_node_ids=[node_id])[0]
    driver.ex_stop_node(node)
    
    flash(Markup('Stopping {}...'.format(node.name)))

    return redirect(url_for('node_details', node_id=node_id))



# redirect to nodes_list
@app.route('/compute/terminate-node/<node_id>')
def terminate_node(node_id):

    driver = get_ec2_driver(get_current_region())

    node = driver.list_nodes(ex_node_ids=[node_id])[0]
    driver.destroy_node(node=node)

    flash(Markup('Terminating {}...'.format(node.name)))

    return redirect(url_for('node_details', node_id=node_id))




# provide details + actions for nodes
@app.route('/compute/node-details/<node_id>')
def node_details(node_id):

    driver = get_ec2_driver(get_current_region())

    node = driver.list_nodes(ex_node_ids=[node_id])[0]

    pretty_node = { 'id': node_id, 'name': node.name, 'state': node.extra['status'], 'type': node.extra['instance_type'], 'zone': node.extra['availability'], 'private_ips': node.private_ips, 'public_ips': node.public_ips, 'private_dns': node.extra['private_dns'], 'public_dns': node.extra['dns_name'], 'security_groups': node.extra['groups'], 'image_id': node.extra['image_id'], 'key': node.extra['key_name']}

    return render_template('node-details.html', node=pretty_node)





@app.route('/compute/node-stats/<node_id>')
def node_stats(node_id):

    driver = get_ec2_driver(get_current_region())

    node = driver.list_nodes(ex_node_ids=[node_id])[0]

    pretty_node = { 'id': node_id, 'name': node.name }
    stats = { 'cpu': { }, 'network_in': { 'data': [], 'labels': [] }, 'network_out': { 'data': [], 'labels': [] }, 'disk_read': { 'data': [], 'labels': [] }, 'disk_write': { 'data': [], 'labels': [] } }

    metrics = ['CPUUtilization', 'NetworkIn', 'NetworkOut', 'DiskReadBytes', 'DiskWriteBytes']
    query = []

    for m in metrics:
        query.append({
            'Id': 'metric_'+m,
            'MetricStat': {
                'Metric': {
                    'Namespace': 'AWS/EC2',
                    'MetricName': m,
                    'Dimensions': [
                        {
                            'Name': 'InstanceId',
                            'Value': node_id
                        },
                    ]
                },
                'Period': 60,
                'Stat': 'Average',
            }
        })

    cloudwatch = boto3.client('cloudwatch', region_name=session['current_region'])
    response = cloudwatch.get_metric_data(
        MetricDataQueries=query,
        StartTime=datetime.now() - timedelta(hours=2),
        EndTime=datetime.now(),
        ScanBy='TimestampAscending'
    )

    for metric in response['MetricDataResults']:
        index = metrics.index(metric['Label'])
        key = [*stats][index]

        stats[key]['data'] = metric['Values']
        stats[key]['labels'] = [ t.strftime('%d-%m-%Y %H:%M') for t in metric['Timestamps'] ]

    return render_template('node-stats.html', node=pretty_node, stats=stats)



if __name__ == '__main__':
    app.run(debug=True)