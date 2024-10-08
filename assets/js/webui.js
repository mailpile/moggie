var moggie_webui;
moggie_webui = (function() {

  var dompurify_config = {
      FORBID_TAGS: ['style', 'script'],
      ALLOW_DATA_ATTR: true,
      ALLOW_UNKNOWN_PROTOCOLS: false,
      WHOLE_DOCUMENT: false
  };

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
    update_a_hrefs: function(scope) {
      $(scope).find('a').click(function(ev) {
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
        else {
          return true;
        }
      });
    },

    replace_content: function(ev) {
      if (ev == 'prep') {
        content_div.innerHTML = '<i class=loading>Loading...</i>';
      } else {
        response = JSON.parse(ev['data']);
        content_div.innerHTML = DOMPurify.sanitize(
          response['html'], dompurify_config);
        moggie_api.record_data(content_div, response['state']);
        moggie_webui.update_a_hrefs(content_div);
      }
    },

    page_setup: function() {
      if (moggie_api.ensure_access_token_not_in_url()) {
        content_div = document.getElementsByClassName('content')[0];
        moggie_api.record_data(content_div, moggie_state);

        _b('div', 'headbar').innerHTML = "<p><a href='/'>Welcome to Moggie</a></p>";
        _b('div', 'sidebar').innerHTML = "<p>Yay a sidebar</p>";

        with_script('/static/js/jquery3.js', function() {
          moggie_api.setup_websocket(function() {
            with_script('/static/js/purify.min.js', function() {
              moggie_webui.update_a_hrefs('body');
            });
          });
        });
      }
    }
  };
})();

moggie_webui.page_setup();
