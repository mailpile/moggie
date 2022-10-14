function create_sidebar() {
    var sidebar = document.createElement('div');
    sidebar.setAttribute('class', 'sidebar');
    sidebar.innerHTML = "<p>This is a sidebar, we love it</p>";
    document.getElementsByTagName('body')[0].appendChild(sidebar);
}

function create_headbar() {
    var headbar = document.createElement('div');
    headbar.setAttribute('class', 'headbar');
    headbar.innerHTML = "<p>Welcome to Moggie!</p>";
    document.getElementsByTagName('body')[0].appendChild(headbar);
}

function setup_websocket() {
    var host = document.location.host;
    var wsp = (document.location.protocol == 'http:') ? 'ws' : 'wss';
    const socket = new WebSocket(wsp + '://' + host + '/ws');
    socket.onopen = function () {
        setInterval(function() {
            socket.send('{"prototype": "ping", "ts": '+ Date.now() +'}');
        }, 5000);
    };
    socket.onmessage = function(event) {
        var data = JSON.parse(event.data);
        if (data['prototype'] == 'pong' && data['ts']) {
            var now = Date.now();
            console.log('Websocket ping time is ' + (now - data['ts']));
        }
        else {
            console.log(event.data)
        }
    };
    // FIXME: Do something sensible when the connection goes away.
}

function ensure_access_token_not_in_url() {
    var path_parts = document.location.pathname.split('/');
    if (path_parts[1] != 'cli') {
        document.cookie = 'moggie_token=' + path_parts[1] + '; SameSite=Strict; path=/';
        path_parts.splice(1, 1)
        document.location.href = path_parts.join('/');
        return false;
    }
    return true;
}

if (ensure_access_token_not_in_url()) {
    create_headbar();
    setup_websocket()
    create_sidebar();
}
