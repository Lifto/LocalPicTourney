from __future__ import division, absolute_import, unicode_literals

# Search notes:
# Tags can be searched by sub-text.
# Search is implemented by keeping the lower-case tag (only) in a postgresql
# database and querying using ILIKE %subtext%, so that any given string will
# match anywhere in the tag. (ex: 'foo' will match 'barfoobaz')
# TODO: Search is split into two databases, a male and female.
# A search returns a list of lower case tags matching the string.
# TODO: rank the results - presently we take 25 matches
# first-come-first-served, with no preference for "better matches"

import psycopg2

from logic.location import get_pg_connection, setup_pg_connection
from log import log
import model
from settings import settings

NAME_SQL = settings.NAME_PREFIX.replace('-', '_')
NAME_SQL = NAME_SQL.replace('.', 'p')
TAG_TABLE_MALE = '{}search_db_m'.format(NAME_SQL)
TAG_TABLE_FEMALE = '{}search_db_f'.format(NAME_SQL)

def _drop_search_db():
    import psycopg2
    for table_name in [TAG_TABLE_MALE, TAG_TABLE_FEMALE]:
        cur = get_pg_connection().cursor()
        try:
            cmd = "DROP TABLE {table_name};".format(table_name=table_name)
            log.info(cmd)
            try:
                cur.execute(cmd)
            except psycopg2.ProgrammingError as e:
                log.exception(e)
            else:
                log.info("table dropped")
        finally:
            cur.close()

def _init_search_db():
    import psycopg2
    cur = get_pg_connection().cursor()
    try:
        cmd = "CREATE EXTENSION btree_gist;"
        log.info(cmd)
        try:
            cur.execute(cmd)
        except psycopg2.ProgrammingError as e:
            log.exception(e)
        else:
            log.info("extension 'btree_gist' added.")

        for table_name in [TAG_TABLE_MALE, TAG_TABLE_FEMALE]:
            cmd = "CREATE TABLE {} (gender_tag VARCHAR(250) PRIMARY KEY);".format(
                table_name)
            log.info(cmd)
            try:
                cur.execute(cmd)
            except psycopg2.ProgrammingError:
                log.info("table {} already exists".format(table_name))
            else:
                log.info("table {} created.".format(table_name))

            # This is for keyword search, which we don't do. Left in as a note.
            # cmd = "CREATE INDEX {table_name}_tsvector_index on {table_name} USING gin(to_tsvector('english', gender_tag))".format(
            #         table_name=TAG_TABLE_NAME
            # )
    finally:
        cur.close()

    for gender_tag in model.PhotoGenderTag.scan():
        gender, tag = gender_tag.gender_tag.split('_', 1)
        add_tag(gender, tag)


def add_tag(gender, tag):
    """Add a tag to the search system.

    gender -- Must be 'm' or 'f'
    tag -- the text of the tag (case is ignored) may be prefixed with a '#'.

    """
    if gender == 'm':
        table_name = TAG_TABLE_MALE
    else:
        table_name = TAG_TABLE_FEMALE
    cmd = "INSERT INTO {table_name} (gender_tag) VALUES ('{tag}');".format(
            table_name=table_name,
            tag=tag.lower())
    last_exception = None
    for retry in range(3):
        cur = None
        try:
            cur = get_pg_connection().cursor()
            cur.execute(cmd)
        except psycopg2.IntegrityError:
            log.debug("Tag already exists")
            break
        except Exception as e:
            log.error("SearchDB had exception %s, attempt %s" % (e, retry))
            last_exception = e
            setup_pg_connection()
        else:
            break
        finally:
            if cur is not None:
                cur.close()
    else:
        log.error("SearchDB connect retried out, re-raising %s" % last_exception)
        raise last_exception


def search(gender, term, count=25):
    """Return list of strings from search system, no prefixed gender.

    gender -- Must be 'm' or 'f'
    term -- return tags containing this string.
    count -- number of tags to return

    """
    # Leaving this in as a note, it is a keyword search. Not quite what we want.
    # cmd="""
    #     SELECT gender_tag
    #     FROM (SELECT gender_tag as gender_tag,
    #                  to_tsvector(gender_tag) as gender_tag_vector
    #           FROM {table_name}) foo
    #     WHERE foo.gender_tag_vector @@ to_tsquery('english', '{term}')
    #     ORDER BY ts_rank(foo.gender_tag_vector, to_tsquery('english', '{term}')) DESC;""".format(
    #     search_index_name=TAG_SEARCH_INDEX_NAME,
    #     table_name=TAG_TABLE_NAME,
    #     term=term)
    if gender == 'm':
        table_name = TAG_TABLE_MALE
    else:
        table_name = TAG_TABLE_FEMALE
    cmd="""
        SELECT gender_tag FROM {table_name} WHERE gender_tag ilike '%{term}%'
    """.format(
        table_name=table_name,
        term=term)

    cur = get_pg_connection().cursor()
    try:
        cur.execute(cmd)
        return [x[0] for x in cur.fetchmany(count)]
    finally:
        cur.close()


def assert_connection():
    search('m', 'dan')
    return True
