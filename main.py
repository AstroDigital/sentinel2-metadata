import os
import json
import boto3
import click
import logging
from copy import copy
import boto
import requests
from boto.s3.key import Key
from collections import OrderedDict
from datetime import datetime, date, timedelta
from sentinel_s3 import range_metadata, single_metadata
import rasterio

from elasticsearch import Elasticsearch, RequestError

bucket_name = os.getenv('BUCKETNAME', 'sentinel-meta')
s3 = boto3.resource('s3')
es_index = 'sat-api'
es_type = 'sentinel2'


def create_index(index_name, doc_type):

    body = {
        doc_type: {
            'properties': {
                'scene_id': {'type': 'string', 'index': 'not_analyzed'},
                'satellite_name': {'type': 'string'},
                'cloud_coverage': {'type': 'float'},
                'date': {'type': 'date'},
                'data_geometry': {
                    'type': 'geo_shape',
                    'tree': 'quadtree',
                    'precision': '5mi'}
            }
        }
    }

    es.indices.create(index=index_name, ignore=400)

    es.indices.put_mapping(
        doc_type=doc_type,
        body=body,
        index=index_name
    )


def meta_constructor(metadata):
    internal_meta = copy(metadata)

    scene_id = 'S2A_tile_{0}_{1}{2}{3}_{4}'.format(
        metadata['date'].replace('-', ''),
        metadata['utm_zone'],
        metadata['latitude_band'],
        metadata['grid_square'],
        int(metadata['aws_path'].split('/')[-1])
    )

    body = OrderedDict([
        ('scene_id', scene_id),
        ('original_scene_id', internal_meta.pop('tile_name')),
        ('satellite_name', metadata.get('spacecraft_name', 'Sentintel-2A')),
        ('cloud_coverage', metadata.get('cloudy_pixel_percentage', 100)),
    ])

    body.update(internal_meta)

    try:
        body['data_geometry'] = body.pop('tile_data_geometry')
    except KeyError:
        pass

    return body


def elasticsearch_updater(product_dir, metadata):
    try:
        body = meta_constructor(metadata)

        try:
            es.index(index=es_index, doc_type=es_type, id=body['scene_id'],
                     body=body)
            print 'Saved: ' + body['scene_id']
        except RequestError:
            body['data_geometry'] = None
            es.index(index=es_index, doc_type=es_type, id=body['scene_id'],
                     body=body)
    except Exception as e:
        print('Unhandled error occured while writing to elasticsearch')
        print('Details: %s' % e.__str__())


def thumbnail_writer(product_dir, metadata):
    """
    Extra function to convert images to JPEG, then upload to S3 and call
    the ES metadata writer afterwards.
    """

    # from main import elasticsearch_updater
    # Download original thumbnail
    orig_url = metadata['thumbnail']
    thumbs_bucket_name = os.getenv('THUMBS_BUCKETNAME', 'ad-thumbnails')

    # Build up output file name
    output_file = str(metadata['utm_zone']) + metadata['latitude_band'] + \
        metadata['grid_square'] + str(metadata['date'].replace('-', '')) + \
        metadata['aws_path'][-1] + '.jpg'

    thumbnail = 'https://' + thumbs_bucket_name + '.s3.amazonaws.com/' + output_file

    # check if outputfile exists
    r = requests.get(thumbnail)
    if r.status_code != 200:

        # Use GDAL to convert to jpg
        with rasterio.drivers():
            with rasterio.open(orig_url) as src:
                r, g, b = src.read()

                # Build up output file name
                output_file = str(metadata['utm_zone']) + metadata['latitude_band'] + \
                    metadata['grid_square'] + str(metadata['date'].replace('-', '')) + \
                    metadata['aws_path'][-1] + '.jpg'

                # Copy and update profile
                profile = {
                    'count': 3,
                    'dtype': 'uint8',
                    'driver': 'JPEG',
                    'height': src.height,
                    'width': src.width,
                    'nodata': 0
                }

                # Write to output jpeg
                with rasterio.open(output_file, 'w', **profile) as dst:
                    dst.write_band(1, r)
                    dst.write_band(2, g)
                    dst.write_band(3, b)

        # Upload thumbnail to S3
        try:
            print('uploading %s' % output_file)
            c = boto.connect_s3()
            b = c.get_bucket(thumbs_bucket_name)
            k = Key(b, name=output_file)
            k.set_metadata('Content-Type', 'image/jpeg')
            k.set_contents_from_file(open(output_file), policy='public-read')
        except Exception as e:
            print(e)

        # Delete thumbnail and associated files
        if os.path.exists(output_file):
            os.remove(output_file)
        if os.path.exists(output_file + '.aux.xml'):
            os.remove(output_file + '.aux.xml')

    # Update metadata record
    metadata['thumbnail'] = thumbnail
    elasticsearch_updater(product_dir, metadata)


def file_writer(product_dir, metadata):
    body = meta_constructor(metadata)

    if not os.path.exists(product_dir):
        os.makedirs(product_dir)

    f = open(os.path.join(product_dir, body['scene_id'] + '.json'), 'w')
    f.write(json.dumps(body))
    f.close()


def s3_writer(product_dir, metadata):
    # make sure product_dir doesn't start with slash (/) or dot (.)
    if product_dir.startswith('.'):
        product_dir = product_dir[1:]

    if product_dir.startswith('/'):
        product_dir = product_dir[1:]

    body = meta_constructor(metadata)

    key = os.path.join(product_dir, body['scene_id'] + '.json')
    s3.Object(bucket_name, key).put(Body=json.dumps(body), ACL='public-read', ContentType='application/json')


def last_updated(today):
    """ Gets the latest time a product added to S3 bucket """

    bucket = s3.Bucket(bucket_name)

    start_day = today.day
    start_month = today.month

    yr_counter = 0
    while True:
        m_counter = 0
        year = today.year - yr_counter
        if year < 2015:
            break
        while True:
            month = start_month - m_counter
            if month == 0:
                start_month = 12
                break
            d_counter = 0
            while True:
                day = start_day - d_counter
                if day == 0:
                    start_day = 31
                    break
                path = os.path.join(str(year), str(month), str(day))
                print('checking %s' % path)
                objs = bucket.objects.filter(Prefix=path).limit(1)
                if list(objs):
                    return date(year, month, day)
                d_counter += 1
            m_counter += 1
        yr_counter += 1

    return None


def convert_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        raise click.UsageError('Incorrect Date format (%s)' % value)


def geometry_check(meta):
    """ Some of tiles located in latitude band of N (MGRS) have an
    incorrect data gometery in the original metadata files. This function flag these tiles
    so they are download and correct geometry is extracted for them using sentine-s3 lib """

    try:
        if meta['latitude_band'] == 'N':
            return True,
    except KeyError:
        pass
    return False


@click.command()
@click.argument('ops', metavar='<operations: choices: s3 | es | disk | thumbs>', nargs=-1)
@click.option('--product', default=None, help='Product name. If given only the given product is processed.')
@click.option('--start', default=None, help='Start Date. Format: YYYY-MM-DD')
@click.option('--end', default=None, help='End Date. Format: YYYY-MM-DD')
@click.option('--concurrency', default=20, type=int, help='Process concurrency. Default=20')
@click.option('--es-host', default='localhost', help='Elasticsearch host address')
@click.option('--es-port', default=9200, type=int, help='Elasticsearch port number')
@click.option('--folder', default='.', help='Destination folder if is written to disk')
@click.option('-v', '--verbose', is_flag=True)
def main(ops, product, start, end, concurrency, es_host, es_port, folder, verbose):

    if not ops:
        raise click.UsageError('No Argument provided. Use --help if you need help')

    accepted_args = {
        'es': elasticsearch_updater,
        's3': s3_writer,
        'disk': file_writer,
        'thumbs': thumbnail_writer
    }

    writers = []
    for op in ops:
        if op in accepted_args.keys():
            writers.append(accepted_args[op])
        else:
            raise click.UsageError('Operation (%s) is not supported' % op)

    logger = logging.getLogger('sentinel.meta.s3')
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()

    if verbose:
        ch.setLevel(logging.INFO)
    else:
        ch.setLevel(logging.ERROR)

    formatter = logging.Formatter('%(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    if product:
        single_metadata(product, folder, writers=writers, geometry_check=geometry_check)
    else:

        if end:
            end = convert_date(end)
        else:
            end = date.today()

        if start:
            start = convert_date(start)
        else:
            delta = timedelta(days=1)
            start = end - delta

        if 'es' in ops or 'thumbs' in ops:
            global es
            es = Elasticsearch([{
                'host': es_host,
                'port': es_port
            }])

            create_index(es_index, es_type)

        range_metadata(start, end, folder, concurrency, writers, geometry_check=geometry_check)


if __name__ == '__main__':
    main()
