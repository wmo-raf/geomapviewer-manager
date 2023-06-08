import glob
import json
import os
import tempfile
from subprocess import Popen, PIPE
from urllib.parse import unquote
from zipfile import ZipFile

from django.db import connection

from geomanager.errors import NoShpFound, NoShxFound, NoDbfFound, InvalidFile

GEOM_TYPES = {
    "POINT": "Point",
    "MULTIPOINT": "MultiPoint",
    "POLYGON": "Polygon",
    "MULTIPOLYGON": "MultiPolygon",
    "LINESTRING": "LineString",
    "MULTILINESTRING": "MultiLineString",
}


def ogr2pg(file_path, table_name, db_settings, srid=4326):
    # construct db connection options from uri
    db_host = db_settings.get("host")
    db_port = db_settings.get("port")
    db_user = db_settings.get("user")
    db_password = db_settings.get("password")
    db_name = db_settings.get("name")
    # unquote incase encoded
    db_password = unquote(db_password)

    pg_service_schema = db_settings.get("pg_service_schema")

    # construct ogr2ogr command
    # notable option: -lco FID=gid  - Use custom fid column name. ogr by default gives ogc_fid. We use gid instead
    cmd = ["ogr2ogr", "-f", "PostgreSQL",
           f"PG:host={db_host} port={db_port} user={db_user} password={db_password} dbname={db_name}", file_path,
           "-nln", table_name, "-lco", f"FID=gid", "-lco", f"SCHEMA={pg_service_schema}", "-lco", "GEOMETRY_NAME=geom",
           "-nlt", "PROMOTE_TO_MULTI", "-lco", "PRECISION=NO"
           ]

    p1 = Popen(cmd, stdout=PIPE, stderr=PIPE)

    p1.stdout.close()

    stdout, stderr = p1.communicate()

    # TODO: Catch possible errors better
    if stderr:
        raise Exception(stderr)

    full_table_name = f"{pg_service_schema}.{table_name}"

    pg_table = {"table_name": full_table_name, "srid": srid}

    info = get_postgis_table_info(pg_service_schema, table_name)

    pg_table.update({**info})

    return pg_table


def extract_zipped_shapefile(shp_zip_path, out_dir):
    # unzip file
    with ZipFile(shp_zip_path, 'r') as zip_obj:
        for filename in zip_obj.namelist():
            # ignore __macosx files
            if not filename.startswith('__MACOSX/'):
                zip_obj.extract(filename, out_dir)

    # Use the first available shp
    shp = glob.glob(f"{out_dir}/*.shp") or glob.glob(f"{out_dir}/*/*.shp")

    if not shp:
        raise NoShpFound("No shapefile found in provided zip file")

    shp_fn = os.path.splitext(shp[0])[0]
    shp_dir = os.path.dirname(shp_fn)

    files = [os.path.join(shp_dir, f) for f in os.listdir(shp_dir)]

    # check for .shx
    if f"{shp_fn}.shx" not in files:
        raise NoShxFound("No .shx file found in provided zip file")

    # check for .dbf
    if f"{shp_fn}.dbf" not in files:
        raise NoDbfFound("No .dbf file found in provided zip file")

    # return first shp path
    return shp[0]


def ogr_db_import(file_path, table_name, db_settings):
    file_extension = os.path.splitext(file_path)[1]

    # handle shapefile
    if file_extension == ".zip":
        with tempfile.TemporaryDirectory() as tmpdir:
            shp = extract_zipped_shapefile(file_path, tmpdir)
            table_info = ogr2pg(shp, table_name, db_settings)

            return table_info

    # handle geojson
    if file_extension == ".geojson":
        table_info = ogr2pg(file_path, table_name, db_settings)
        return table_info

    # handle geopackage
    if file_extension == ".gpkg":
        table_info = ogr2pg(file_path, table_name, db_settings)
        return table_info

    raise InvalidFile(message='Unsupported file type')


def get_postgis_table_info(schema, table_name):
    # Get all the column names with corresponding data types
    with connection.cursor() as cursor:
        columns_sql = f"""SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = '{table_name}' AND table_schema = '{schema}'
        """
        cursor.execute(columns_sql)
        results = cursor.fetchall()
        column_data_types = [{"name": row[0], "data_type": row[1]} for row in results if row[0] != "geom"]

    # # Get the extents of all the data in the table
    with connection.cursor() as cursor:
        extent_sql = f"SELECT ST_Extent(geom) FROM {schema}.{table_name}"
        cursor.execute(extent_sql)
        bbox_text = cursor.fetchone()[0]
        bbox_str = bbox_text.replace("BOX(", "").replace(")", "").replace(" ", ",").split(",")

    # Get the geometry type of the 'geom' column
    with connection.cursor() as cursor:
        geom_sql = f"""SELECT GeometryType(geom)
        FROM {schema}.{table_name} LIMIT 1
        """
        cursor.execute(geom_sql)
        result = cursor.fetchone()[0]
        geometry_type = result.replace('ST_', '')

    return {
        'properties': column_data_types,
        'bounds': bbox_str,
        'geom_type': GEOM_TYPES[geometry_type],
    }


def drop_vector_table(table_name):
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {table_name};")


def create_feature_collection_from_geom(geom):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(geom.geojson),
                "properties": {}
            }
        ]
    }
