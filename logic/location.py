from __future__ import division, absolute_import, unicode_literals



import codecs

from log import log
from settings import settings
import threading
from uuid import UUID

import psycopg2


PG_CONNECTION = threading.local()
PG_CONNECTION = None


LOCATIONS = {}  # uuid -> Location, initialized at bottom from locations.csv

def setup_pg_connection():
    global PG_CONNECTION
    if settings.LOCATION_DB_ENABLED:
        # This gives unicode strings in results instead of utf-8 strings
        psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
        psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)
        PG_CONNECTION = psycopg2.connect(
                database=settings.LOCATION_DB_NAME,
                user=settings.LOCATION_DB_USER,
                password=settings.LOCATION_DB_PASSWORD,
                host=settings.LOCATION_DB_HOST,
                port=settings.LOCATION_DB_PORT)
        log.info('connection established')
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        PG_CONNECTION.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    else:
        PG_CONNECTION = MockConnection()

def get_pg_connection():
    if PG_CONNECTION is None:
        setup_pg_connection()
    return PG_CONNECTION


class Geo(object):
    def __init__(self, lat, lon, meta):
        self.lat = lat
        self.lon = lon
        self.meta = meta

    @staticmethod
    def from_string(s):
        split = s.split(';')
        lat = float(split[0])
        lon = float(split[1])
        return Geo(lat, lon, s)


class Location(object):
    def __init__(self, uuid, country, city, accent_city, region, population,
                 notes, lat, lon):
        self.uuid = uuid
        self.country = country
        self.city = city
        self.accent_city = accent_city
        self.region = region
        self.population = population
        self.notes = notes
        self.lat = lat
        self.lon = lon

    @staticmethod
    def from_geo(geo):
        cmd = "SELECT id, country, city, accent_city, region, population, notes FROM cities ORDER BY geom <-> ST_GeomFromText('POINT({lat} {lon})', 4326) LIMIT 1".format(lat=geo.lat, lon=geo.lon)
        last_exception = None
        for retry in range(3):
            try:
                cur = get_pg_connection().cursor()
                cur.execute(cmd)
            except Exception as e:
                log.error("LocationDB had exception %s, attempt %s" % (e, retry))
                log.exception(e)
                last_exception = e
                setup_pg_connection()
            else:
                break
        else:
            log.error("LocationDB connect retried out, re-raising %s" % last_exception)
            raise last_exception
        uuid, country, city, accent_city, region, population, notes = cur.fetchone()
        uuid = UUID(uuid)
        return LOCATIONS[uuid]

    def _make_geo_meta(self):
        """For test purposes, make location header value for this location."""
        return "{lat};{lon};0.0 hdn=-1.0 spd=0.0".format(lat=self.lat,
                                                         lon=self.lon)

def get_location_name(location_uuid):
    location = LOCATIONS.get(location_uuid)
    if location:
        return location.accent_city
    else:
        return ''


with codecs.open('locations.csv', 'r', 'utf-8') as f:
    while True:
        l = f.readline()
        if not l:
            break
        should_include, country, city, accent_city, region, population, lat, lon, uuid, notes = l.split(',')

        if should_include == 't':
            location_uuid = UUID(uuid)
            location = Location(location_uuid, country, city, accent_city,
                                region, population, notes, lat, lon)
            LOCATIONS[location_uuid] = location


class MockConnection(object):
    def cursor(self):
        return MockCursor()

class MockCursor(object):
    def execute(self, cmd):
        if cmd == "SELECT id, country, city, accent_city, region, population, notes FROM cities ORDER BY geom <-> ST_GeomFromText('POINT(34.33233141 -118.0312186)', 4326) LIMIT 1":
            self.next_result = (
                '67f22847ecf311e4a264c8e0eb16059b',
                'us', 'los angeles', 'Los Angeles', 'CA', 3877129, '')
        else:
            self.next_result = (
                '67f22847ecf311e4a264c8e0eb16059b',
                'us', 'los angeles', 'Los Angeles', 'CA', 3877129, '')

    def fetchone(self):
        return self.next_result

    def fetchmany(self, count):
        return []

    def close(self):
        pass

def assert_connection():
    la_str = "34.33233141;-118.0312186;0.0 hdn=-1.0 spd=0.0"
    geo = Geo.from_string(la_str)
    Location.from_geo(geo)
    return True

def _init_location_db():
    log.info("Initializing location and search databases.")

    import codecs
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    # Is the database prepared for geography?
    # Note: Connect to database named 'postgres', not our ocean db.
    conn = psycopg2.connect(database='postgres',
                            user=settings.LOCATION_DB_USER,
                            password=settings.LOCATION_DB_PASSWORD,
                            host=settings.LOCATION_DB_HOST,
                            port=settings.LOCATION_DB_PORT)
    log.info("connected")

    log.info("setting isolation level")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    log.info("isolation level set, getting cursor...")
    cur = conn.cursor()

    def execute(cmd):
        log.info(cmd)
        try:
            cur.execute(cmd)
        except psycopg2.ProgrammingError as e:
            log.info("Got {}, ignoring".format(e))

    execute('DROP DATABASE {}'.format(settings.LOCATION_DB_NAME))
    execute('CREATE DATABASE {}'.format(settings.LOCATION_DB_NAME))
    cur.close()
    conn.close()
    conn = psycopg2.connect(database=settings.LOCATION_DB_NAME,
                            user=settings.LOCATION_DB_USER,
                            password=settings.LOCATION_DB_PASSWORD,
                            host=settings.LOCATION_DB_HOST,
                            port=settings.LOCATION_DB_PORT)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    execute('CREATE EXTENSION postgis;')
    execute('CREATE EXTENSION postgis_topology;')
    execute('CREATE EXTENSION fuzzystrmatch;')
    execute('create extension postgis_tiger_geocoder;')
    execute('alter schema tiger owner to rds_superuser;')

    log.info("closing cursor")
    cur.close()
    log.info("clossing postgres connection")
    conn.close()
    log.info("connection closed")

    # cmd = 'ALTER SCHEMA mydb1 => alter schema topology owner to rds_superuser;'
    #
    # cmd = """
    #     CREATE FUNCTION exec(text) returns text language plpgsql volatile AS $f$ BEGIN EXECUTE $1; RETURN $1; END; $f$;
    #     SELECT exec('ALTER TABLE ' || quote_ident(s.nspname) || '.' || quote_ident(s.relname) || ' OWNER TO rds_superuser')
    #       FROM (
    #         SELECT nspname, relname
    #         FROM pg_class c JOIN pg_namespace n ON (c.relnamespace = n.oid)
    #         WHERE nspname in ('tiger','topology') AND
    #         relkind IN ('r','S','v') ORDER BY relkind = 'S')
    #     s;
    #     """
    # cmd = "SELECT topology.CreateTopology('localpictourney_topo', 4326, 0.00001);"

#--------------------------------
    log.info("opening locations.csv")
    with codecs.open('locations.csv', 'r', 'utf-8') as f:
        log.info("file opened")
        conn = psycopg2.connect(database=settings.LOCATION_DB_NAME,
                                user=settings.LOCATION_DB_USER,
                                password=settings.LOCATION_DB_PASSWORD,
                                host=settings.LOCATION_DB_HOST,
                                port=settings.LOCATION_DB_PORT)
        log.info("connection established")
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        log.info("cursor created")
        try:
            cmd = "DROP TABLE cities;"
            log.info(cmd)
            cur.execute(cmd)
            log.info("table 'cities' dropped")
        except psycopg2.ProgrammingError:
            log.info("table 'cities' was not in database")
        cmd = """
          CREATE TABLE cities (
              id UUID PRIMARY KEY,
              geom GEOMETRY(Point, 4326),
              country VARCHAR(128),
              city VARCHAR(128),
              accent_city VARCHAR(128),
              region VARCHAR(128),
              population BIGINT,
              notes VARCHAR(128));
          """
        log.info(cmd)
        cur.execute(cmd)
        cmd = "CREATE INDEX cities_gix ON cities USING GIST (geom);"
        log.info(cmd)
        cur.execute(cmd)

        log.info("reading file...")
        while True:
            l = f.readline()
            if not l:
                break
            log.info(l)
            should_include, country, city, accent_city, region, population, lat, lon, uuid, notes = l.split(',')

            if should_include == 't':
                cmd = "INSERT INTO cities (id, geom, country, city, accent_city, region, population, notes) VALUES ('{uuid}', ST_GeomFromText('POINT({lat} {lon})', 4326), '{country}', '{city}', '{accent_city}', '{region}', '{population}', '{notes}');".format(
                    uuid=uuid, country=country, city=city, accent_city=accent_city,
                    region=region, population=int(population), lat=lat, lon=lon,
                    notes=notes)
                log.info(cmd)
                cur.execute(cmd)

        log.info("file read done.")
        cur.close()
        conn.close()
        log.info("location data initialized")

    # LocationDB
    # had
    # exception
    # relation
    # "cities"
    # does
    # not exist
    # LINE
    # 1: ...
    # city, accent_city, region, population, notes
    # FROM
    # cities
    # ORD...

    return #remove this
    #----------------

#--------
#     # FATAL:  database "loc" does not exist
#     import psycopg2
#     log.info('psycopg2 imported')
#     # database = settings.LOCATION_DB_NAME,
#     # user = settings.LOCATION_DB_USER,
#     # password = settings.LOCATION_DB_PASSWORD,
#     # host = settings.LOCATION_DB_HOST,
#     # port = settings.LOCATION_DB_PORT)
#
#     # Note we connect to the database 'postgres'
#     # and not settings.LOCATION_DB_NAME
#     conn = psycopg2.connect(database='postgres',
#                             user=settings.LOCATION_DB_USER,
#                             password = settings.LOCATION_DB_PASSWORD,
#                             host = settings.LOCATION_DB_HOST,
#                             port = settings.LOCATION_DB_PORT)
#     log.info('connection made')
#     from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
#     conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
#     cur = conn.cursor()
#     cmd = 'CREATE DATABASE {}'.format(settings.LOCATION_DB_NAME)
#     log.info("calling '{}'".format(cmd))
#     cur.execute(cmd)
#     cur.close()
#     conn.close()
#     log.info("database '{}' created".format(settings.LOCATION_DB_NAME))
# #
# # except Exception as e:
# # logger.error(e)
# # return {'status': 'ERR', 'message': str(e)}
# #
# # ---
#     cmd = 'CREATE EXTENSION postgis;'
#     log.info(cmd)
#     cur.execute(cmd)
#     cmd = 'CREATE EXTENSION postgis_topology;'
#     log.info(cmd)
#     cur.execute(cmd)
#
# #
# # to get result, you need to fetch from the cursor
# #
# # cur.execute('select current_user;')
# # result = cur.fetchall()
# #
# # oh, we also needed create extension fuzzystrmatch;
#
#     cmd = 'create extension postgis_tiger_geocoder;'
#     log.info(cmd)
#     cur.execute(cmd)
# #
# # and did this(see
# # http: // docs.aws.amazon.com / AmazonRDS / latest / UserGuide / Appendix.PostgreSQL.CommonDBATasks.html
# # # Appendix.PostgreSQL.CommonDBATasks.PostGIS)
# #
#
#     cmd = 'alter schema tiger owner to rds_superuser;'
#     log.info(cmd)
#     cur.execute(cmd)
#
#     cmd = 'ALTER SCHEMA mydb1 => alter schema topology owner to rds_superuser;'
#
#     cmd = """
# CREATE FUNCTION exec(text) returns text language plpgsql volatile AS $f$ BEGIN EXECUTE $1; RETURN $1; END; $f$;
# SELECT exec('ALTER TABLE ' || quote_ident(s.nspname) || '.' || quote_ident(s.relname) || ' OWNER TO rds_superuser')
#   FROM (
#     SELECT nspname, relname
#     FROM pg_class c JOIN pg_namespace n ON (c.relnamespace = n.oid)
#     WHERE nspname in ('tiger','topology') AND
#     relkind IN ('r','S','v') ORDER BY relkind = 'S')
# s;
# """
# #
# # Well, that
# # amazon
# # example
# # worked.How
# # do
# # I
# # add
# # points and get
# # distance?
# #
# # create
# # a
# # topology
# # with this as our spacial reference sytem
# # http://
# #     spatialreference.org / ref / epsg / 4326 /
# #     http: // www.postgis.net / docs / CreateTopology.html
#     cmd = 'SELECT topology.CreateTopology('localpictourney_topo', 4326, 0.00001);'
# #
# # when
# # I
# # do
# # that, I
# # get
# # {u'status': u'ERR',
# #  u'message': u'FATAL:  password authentication failed for user "rds_superuser"\nFATAL:  password authentication failed for user "rds_superuser"\n'}
# #
# # maybe
# # aws
# # docs
# # were
# # pulling
# # my
# # chain, or I
# # 'm doing things differently? I think I have to start over.
# #
# # hmmm...getting
# # same
# # error.
# #     permission
# # denied
# # for schema topology
# #
# # hmmm...get this if I run the tiger example without the permission stuff above.Maybe I did the permissions wrong?
# #
# # If you get a command not found thing for the normalize address, do this
# # SET search_path="$user", public, topology, tiger;
# # --------
# # this is what I need to do
# # -- Create table with spatial column
# # CREATE TABLE hotcities (
# # id SERIAL PRIMARY KEY,
# # geom GEOMETRY(Point, 26910),
# # name VARCHAR(128)
# # );
# #
# # -- Add a spatial index
# # CREATE INDEX hotcities_gix
# # ON mytable
# # USING GIST (geom);
# #
# # -- Add a point
# # INSERT INTO hotcities (geom) VALUES (
# # ST_GeomFromText('POINT(0 0)', 26910)
# # );
# #
# # -- Query for nearby points
# # SELECT id, accent_city
# # FROM hotcities
# # WHERE ST_DWithin(
# # geom,
# # ST_GeomFromText('POINT(0 0)', 26910),
# # 1000
# # );
# #
# # ----- that works, but this is more to our spec
# # Country, City, AccentCity, Region, Population, Latitude, Longitude, notes
# #
# # CREATE TABLE cities (
# # id SERIAL PRIMARY KEY,
# # geom GEOMETRY(Point, 4326),
# # country VARCHAR(128),
# # city VARCHAR(128),
# # accent_city VARCHAR(128),
# # region VARCHAR(128),
# # population BIGINT,
# # notes VARCHAR(128)
# # );
# #
# # CREATE INDEX cities_gix
# # ON cities
# # USING GIST (geom);
# #
# # INSERT INTO cities (geom, country, city, accent_city, region, population, notes) VALUES (ST_GeomFromText('POINT(35.7719444 -78.6388889)', 4326), 'us', 'raleigh', 'Raleigh', 'NC', '338759', '?maybe
# # ');
# #
# # SELECT * FROM cities ORDER BY geom < -> ST_GeomFromText('POINT(37 -80)', 4326) LIMIT 10;
# #
# #
