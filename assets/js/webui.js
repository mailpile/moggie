var moggie_api;
moggie_api = (function() {

  var moggie_ws;
  var moggie_ws_callbacks = {};
  var cache_ver = '?ts=' + Date.now();
  var next_id = Date.now() % 100000;

  function _b(tag, idName, className) {
    var obj = document.createElement(tag);
    if (idName) {
      obj.setAttribute('id', idName);
      obj.setAttribute('class', idName);
    }
    if (className) {
      obj.setAttribute('class', className);
    }
    document.getElementsByTagName('body')[0].appendChild(obj);
    return obj;
  }
  var _added_css = {};
  function _add_command_css(command) {
    var url = '/themed/css/'+ command +'.css'+ cache_ver;
    if (!_added_css[url]) {
      var obj = document.createElement('link');
      obj.setAttribute('rel', 'stylesheet');
      obj.setAttribute('href', url);
      document.head.appendChild(obj);
      _added_css[url] = true;
    }
  }

  function setup_websocket(on_connected) {
    var ws_status = _b('div', 'websocket_status');
    ws_status.innerHTML = 'offline';

    var host = document.location.host;
    var wsp = (document.location.protocol == 'http:') ? 'ws' : 'wss';
    moggie_ws = new WebSocket(wsp + '://' + host + '/ws');
    moggie_ws.send_json = function(data) {
      this.send(JSON.stringify(data));
    };
    moggie_ws.pinger = function() {
      moggie_ws.send_json({prototype: "ping", ts: Date.now()});
    };
    moggie_ws.onopen = function () {
      ws_status.innerHTML = 'connected';
      ws_status.setAttribute('class', 'slow');
      setInterval(moggie_ws.pinger, 7500);
      moggie_ws.pinger();
      if (on_connected) on_connected();
    };
    moggie_ws.onmessage = function(event) {
      var data = JSON.parse(event.data);
      if (data['prototype'] == 'pong' && data['ts']) {
        var now = Date.now();
        var lag = Date.now() - data['ts'];
        ws_status.innerHTML = 'lag: ' + lag + 'ms';
        ws_status.setAttribute('class',
          (lag < 500) ? 'ok' : ((lag < 1500) ? 'slow' : 'bad'));
      }
      else {
        callback = moggie_ws_callbacks[data['req_id']];
        if (callback) {
          console.log(callback[1] +' took '+ (Date.now() - callback[0]) +'ms');
          delete moggie_ws_callbacks[data['req_id']];
          callback[2](data);
        } else {
          console.log(event.data)
        }
      }
    };
    // FIXME: Do something sensible when the connection goes away.
  }

  function ensure_access_token_not_in_url() {
    var path_parts = document.location.pathname.split('/');
    if ((path_parts.length > 0) && (path_parts[1][0] == '@')) {
      // This is a session cookie.
      // FIXME: Offer the user to "stay logged in."
      document.cookie = 'moggie_token=' + path_parts[1] + '; SameSite=Strict; path=/';
      path_parts.splice(1, 1)
      document.location.href = path_parts.join('/');
      return false;
    }
    return true;
  }

  function with_script(url, next_steps) {
    var sobj = document.createElement('script');
    sobj.onload = next_steps;
    sobj.src = url;
    document.head.appendChild(sobj);
  }

  function _record_data(elem, data) {
    _id = next_id++;
    elem.dataset['moggie'] = _id;
    moggie_api.records[_id] = data;
  }

  return {
    records: {},
    page_setup: function() {
      if (ensure_access_token_not_in_url()) {
        var c1 = document.getElementsByClassName('content')[0];
        _record_data(c1, moggie_state);

        _b('div', 'headbar').innerHTML = "<p>Welcome to Moggie</p>";
        _b('div', 'sidebar').innerHTML = "<p>Yay a sidebar</p>";

        with_script('/static/js/jquery3.js', function() {
          setup_websocket(function() {
            var c2 = _b('div', 'content2', 'content');
            c2.innerHTML = '<i>loading...</i>';
            moggie_api.cli('search',
              ['--format=jhtml', '--limit=50', 'in:inbox'],
              function(d) {
                response = JSON.parse(d['data']);
                c2.innerHTML = response['html'];
                _record_data(c2, response['state']);
              }, 'json');
          });
        });

      }
    },

    cli: function(command, args, callback) {
      var now = Date.now();
      var req_id = 'cli-' + now;
      moggie_ws_callbacks[req_id] = [now, 'cli:'+command, callback];
      moggie_ws.send_json({
        prototype: 'cli',
        req_id: req_id,
        command: command,
        args: args
      });
      _add_command_css(command);
    }
  };
})();

moggie_api.page_setup();
