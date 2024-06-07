from __future__ import division, absolute_import, unicode_literals



from uuid import UUID

from flask import abort, Blueprint, render_template, request
from flask_restplus import reqparse
from jinja2 import TemplateNotFound
from pynamodb.models import DoesNotExist

from log import log
import model
from model import Flag, FlagStatus, Photo, PhotoComment, User
from util import now


admin_blueprint = Blueprint('admin', __name__, template_folder='templates')

def render_model(model_instance):
    model_cls = type(model_instance)
    attrs = model_cls._get_attributes()
    return { a: getattr(model_instance, a) for a in attrs}

def flag_photo(photo_uuid, flagger_uuid, reason, ip_addr):
    return flag('Photo',
                '%s%s' % ('Photo', photo_uuid.hex),
                flagger_uuid,
                reason,
                ip_addr)

def flag_comment(comment_uuid, flagger_uuid, reason, ip_addr):
    return flag('PhotoComment',
         '%s%s' % ('PhotoComment', comment_uuid.hex),
         flagger_uuid,
         reason,
         ip_addr)

def flag_user(user_uuid, flagger_uuid, reason, ip_addr):
    return flag('User',
                '%s%s' % ('User', user_uuid.hex),
                flagger_uuid,
                reason,
                ip_addr)

def flag(kind, kind_id, flagger_uuid, reason, ip_addr):
    try:
        Flag.get(kind_id, flagger_uuid)
    except DoesNotExist:
        flag = Flag(kind_id, flagger_uuid, created_on=now(),
                    reason=reason, ip=ip_addr)
        flag.save()
    else:
        log.info(
            'User can only make one flag per item. User: %s, kind_id: %s',
            (flagger_uuid.hex, kind_id))
        abort(409)

    try:
        flag_status = FlagStatus.get(kind_id)
    except DoesNotExist:
        flag_status = FlagStatus(
            kind_id, kind=kind, flag_count=1, history_count=0,
            status='needs review', history_updated_on=now())
    else:
        # todo confirm this is the correct atomic increment syntax.
        flag_status.flag_count += 1
    flag_status.save()
    return flag



admin_parser = reqparse.RequestParser()
admin_parser.add_argument('user_name',
                          type=str,
                          help='user name of User to look up.',
                          required=False,
                          location='values',
                          default='')
@admin_blueprint.route('/')
def show_index():
    user_flags = list(FlagStatus.count_index.query('User', limit=51, consistent_read=False))
    photo_flags = list(FlagStatus.count_index.query('Photo', limit=51, consistent_read=False))
    comment_flags = list(FlagStatus.count_index.query('PhotoComment', limit=51, consistent_read=False))
    log.info(user_flags)
    args = admin_parser.parse_args()
    log.info(args)
    user_name = args.get('user_name', '')
    user_name_obj = None
    user = None
    if user_name:
        log.info('user_name {}'.format(user_name))
        try:
            user_name_obj = model.UserName.get(user_name)
        except DoesNotExist:
            pass
    if user_name_obj:
        log.info('found user_name obj, for user {}'.format(
            user_name_obj.user_uuid.hex))
        try:
            user = model.User.get(user_name_obj.user_uuid)
        except DoesNotExist:
            pass
    if user:
        log.info('found user obj {}'.format(user.uuid.hex))
        user_dict = render_model(user)
    else:
        user_dict = {}

    template_args = {
        'user_flags': user_flags,
        'photo_flags': photo_flags,
        'comment_flags': comment_flags,
        'user_dict': user_dict
    }
    log.info(template_args)
    return render_template('index.html', **template_args)

@admin_blueprint.route('/flags')
def flags():
    return render_template('flags.html')

@admin_blueprint.route('/users')
def users():
    user_flags = list(FlagStatus.count_index.query('User',
                                                   limit=51,
                                                   scan_index_forward=False,
                                                   consistent_read=False))
    users = []
    for user_flag in user_flags:
        users.append(User.get(UUID(user_flag.kind_id[len('User'):]),
                              consistent_read=False))
    items = []
    for user, flag_status in zip(users, user_flags):
        items.append({
            'user': user,
            'flag_status': flag_status
        })
    template_args = {
        'items': items
    }
    return render_template('users.html', **template_args)

@admin_blueprint.route('/photos')
def photos():
    photo_flags = list(FlagStatus.count_index.query('Photo',
                                                    limit=51,
                                                    scan_index_forward=False,
                                                    consistent_read=False))
    photos = []
    for photo_flag in photo_flags:
        photo_uuid = UUID(photo_flag.kind_id[len('Photo'):])
        try:
            # I don't know why this has to be limit=2, but limit=1 gives nada.
            p = list(PhotoComment.uuid_index.query(photo_uuid, limit=2,
                                                   consistent_read=False))[0]
            photos.append(p)
        except (DoesNotExist, IndexError):
            log.error('Could not find photo %s' % photo_uuid)
            photos.append(None)

    items = []
    for photo, flag_status in zip(photos, photo_flags):
        items.append({
            'photo': photo,
            'flag_status': flag_status
        })
    template_args = {
        'items': items
    }
    return render_template('photos.html', **template_args)


@admin_blueprint.route('/photo_comments')
def photo_comments():
    comment_flags = list(FlagStatus.count_index.query('PhotoComment',
                                                      limit=51,
                                                      scan_index_forward=False,
                                                      consistent_read=False))
    comments = []
    for comment_flag in comment_flags:
        comment_uuid = UUID(comment_flag.kind_id[len('PhotoComment'):])
        try:
            # I don't know why this has to be limit=2, but limit=1 gives nada.
            c = list(PhotoComment.uuid_index.query(comment_uuid, limit=2,
                                                   consistent_read=False))[0]
            comments.append(c)
        except (DoesNotExist, IndexError):
            log.error('Could not find photo comment %s' % comment_uuid)
            comments.append(None)

    items = []
    for comment, flag_status in zip(comments, comment_flags):
        items.append({
            'comment': comment,
            'flag_status': flag_status
        })
    template_args = {
        'items': items
    }
    return render_template('photo_comments.html', **template_args)

