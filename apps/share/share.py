from __future__ import division, absolute_import, unicode_literals



import uuid

from flask import abort, Blueprint, render_template
from flask import request
import model

from log import log

share_blueprint = Blueprint('share', __name__, template_folder='templates')

@share_blueprint.route('/<string:share_code>')
def show_index(share_code):
    try:
        photo_uuid = uuid.UUID(share_code)
    except ValueError:
        abort(404)

    photo = model.get_one(model.Photo, 'uuid_index', photo_uuid)
    if photo is None:
        abort(404)

    template_args = {'share_code': share_code}
    return render_template('share_index.html', **template_args)

