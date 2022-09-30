# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Moggie (General)
"""
# This is a proof of concept moggie plugin for SearX.
#
# To enable this plugin:
#
#  1. Configure Moggie to use a fixed port or public kite name
#  2. Create an access grant to search the subset of mail you want in SearX
#  3. cp moggie.py /path/to/searx-src/searx/engines/
#  4. Edit /path/to/searx-src/searx/settings.yml
#
# In final step, you want to add the following lines (uncommented) to
# the `engines` section of settings.yml.
#
#  - name : moggie
#    engine : moggie
#    paging : True
#    base_url : 'http://127.0.0.1:8025/YOURTOKEN/'
#    disabled : False
#    enable_http : True
#    categories : general
#    shortcut : moggie
#
# Note that you will need to customzie the URL to match your setup. For
# details on how to create access grants and configure moggie, consult the
# search integration guide (link below).
#
# Further details/examples can be found here:
#   - https://github.com/BjarniRunar/moggie/blob/master/docs/search-integration.md
#   - https://github.com/BjarniRunar/searx-moggie-integration/commit/e07b9fd72ec0b39d92128865dbfe222d559e7d1f
#
from json import loads
from urllib.parse import urlencode

# about
about = {
    "website": 'https://github.com/BjarniRunar/moggie',
    "wikidata_id": None,
    "official_api_documentation": 'https://github.com/BjarniRunar/moggie',
    "use_official_api": True,
    "require_api_key": False,
    "results": 'JSON',
}

# engine dependent config
categories = ['general']
paging = True

# search-url
base_url = None
search_path = 'cli/search?{query}&offset={skip}&limit={limit}&format=json'
show_path = '/cli/show/--format=text/'


# do search-request
def request(query, params):
    limit = 5
    skip = (int(params['pageno'])-1) * limit
    params['url'] = base_url +\
        search_path.format(query=urlencode({'q': query}),
                           skip=skip,
                           limit=limit)

    return params


# get response from search-request
def response(resp):
    results = []
    json = loads(resp.text)

    # parse results
    for r in json:
        _id = r['query'][0]
        tokenless_url = '/'.join(base_url.split('/')[:-2])
        results.append({
            'title': r['subject'],
            'content': '%(authors)s (%(date_relative)s)' % r,
            'url': tokenless_url + show_path + _id,
            'cached_url': None,
        })

    # return results
    return results
