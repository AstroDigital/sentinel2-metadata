import os
import rasterio
import boto3

thumbs_bucket_name = os.getenv('THUMBS_BUCKETNAME', 'ad-thumbnails')
s3 = boto3.resource('s3')


def thumbnail_writer(product_dir, metadata):
    """
    Extra function to convert images to JPEG, then upload to S3 and call
    the ES metadata writer afterwards.
    """

    from main import elasticsearch_updater
    # Download original thumbnail
    orig_url = metadata['thumbnail']

    # Use GDAL to convert to jpg
    with rasterio.drivers():
        with rasterio.open(orig_url) as src:
            r, g, b = src.read()

            # Build up output file name
            output_file = str(metadata['utm_zone']) + metadata['latitude_band'] + \
                metadata['grid_square'] + str(metadata['date'].replace('-', '')) + \
                metadata['path'][-1] + '.jpg'

            # Copy and update profile
            profile = src.profile
            profile.update(driver='JPEG')

            # Write to output jpeg
            with rasterio.open(output_file, 'w', **profile) as dst:
                dst.write_band(1, r)
                dst.write_band(2, g)
                dst.write_band(3, b)

    # Upload thumbnail to S3
    s3.Object(thumbs_bucket_name, output_file).put(Body=open(output_file),
                                                   ACL='public-read',
                                                   ContentType='image/jpeg')

    # Delete thumbnail and associated files
    if os.path.exists(output_file):
        os.remove(output_file)
    if os.path.exists(output_file + '.aux.xml'):
        os.remove(output_file + '.aux.xml')

    # Update metadata record
    metadata['thumbnail'] = 'https://' + thumbs_bucket_name + \
        '.s3.amazonaws.com/' + output_file
    elasticsearch_updater(product_dir, metadata)
