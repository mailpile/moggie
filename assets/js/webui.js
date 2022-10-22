var moggie_webui;
moggie_webui = (function() {

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

  function with_script(url, next_steps) {
    var sobj = document.createElement('script');
    sobj.onload = next_steps;
    sobj.src = url;
    document.head.appendChild(sobj);
  }

  var content_div;
  return {
    replace_content: function(ev) {
      if (ev == 'prep') {
        content_div.innerHTML = '<i class=loading>Loading...</i>';
      } else {
        response = JSON.parse(ev['data']);
        content_div.innerHTML = response['html'];
        moggie_api.record_data(content_div, response['state']);
      }
    },

    page_setup: function() {
      if (moggie_api.ensure_access_token_not_in_url()) {
        content_div = document.getElementsByClassName('content')[0];
        moggie_api.record_data(content_div, moggie_state);

        _b('div', 'headbar').innerHTML = "<p>Welcome to Moggie</p>";
        _b('div', 'sidebar').innerHTML = "<p>Yay a sidebar</p>";

        with_script('/static/js/jquery3.js', function() {
          moggie_api.setup_websocket(function() {
            $('a').click(function(ev) {
              var target = $(this).attr('href');
              if (target.startsWith('/cli')) {
                console.log('clicked: ' + this);
                ev.preventDefault();
                var args = target.substring(5).split('/')
                var cmd = args.shift();
                args.push('--format=jhtml');
                moggie_api.cli(cmd, args, moggie_webui.replace_content); 
                return false;
              }
              return true;
            });
/*
            var c2 = _b('div', 'content2', 'content');
            moggie_api.cli('search',
              ['--format=jhtml', '--limit=50', 'in:inbox'],
              moggie_webui.replace_content);
 */
          });
        });
      }
    }
  };
})();

moggie_webui.page_setup();
