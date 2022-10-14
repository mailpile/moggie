var moggie_api = (function() {

  function el(tag, idName, className) {
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

  function setup_websocket() {
    var ws_status = el('div', 'websocket_status');
    ws_status.innerHTML = 'offline';

    var host = document.location.host;
    var wsp = (document.location.protocol == 'http:') ? 'ws' : 'wss';
    const socket = new WebSocket(wsp + '://' + host + '/ws');
    socket.onopen = function () {
      ws_status.innerHTML = 'connected';
      ws_status.setAttribute('class', 'slow');
      setInterval(function() {
        socket.send('{"prototype": "ping", "ts": '+ Date.now() +'}');
      }, 7500);
    };
    socket.onmessage = function(event) {
      var data = JSON.parse(event.data);
      if (data['prototype'] == 'pong' && data['ts']) {
        var now = Date.now();
        var lag = Date.now() - data['ts'];
        ws_status.innerHTML = 'lag: ' + lag + 'ms';
        ws_status.setAttribute('class',
          (lag < 500) ? 'ok' : ((lag < 1500) ? 'slow' : 'bad'));
      }
      else {
        console.log(event.data)
      }
    };
    // FIXME: Do something sensible when the connection goes away.
  }

  function ensure_access_token_not_in_url() {
    var path_parts = document.location.pathname.split('/');
    if ((path_parts.length > 0) && (path_parts[1][0] == '@')) {
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

  return {
    page_setup: function() {
      if (ensure_access_token_not_in_url()) {
        el('div', 'headbar').innerHTML = "<p>Welcome to Moggie</p>";
        el('div', 'sidebar').innerHTML = "<p>Yay a sidebar</p>";

        with_script('/static/js/jquery3.js', function() {
          setup_websocket();

          var c2 = el('div', 'content2', 'content');
          c2.innerHTML = '<i>loading...</i>';

          $.get('/cli/search/--format=jhtml/--limit=25/from:bre/date:2009', function(d) {
            c2.innerHTML = d['html'];
          }, 'json');
        });

      }
    }
  };
})();

moggie_api.page_setup();
