/*
 * Clipboard paste for the latitude/longitude pair rendered by core.fields.PointWidget.
 * Ctrl+V into either input splits the pair across both; the 📋 button does the same for a
 * click, where the browser allows reading the clipboard on its own.
 *
 * Accepts anything that boils down to two signed decimal numbers, latitude first:
 * "48.123456, 19.12345" (what Google Maps copies), the degree-and-hemisphere form this
 * admin's own list display produces ("48.123456° N, 19.12345° E"), or the two numbers
 * separated by whitespace or a semicolon.
 */
(function () {
    'use strict';

    var NUMBER = '([+-]?\\d+(?:\\.\\d+)?)\\s*°?\\s*([NSEWnsew])?';
    var COORDINATES = new RegExp('^\\s*' + NUMBER + '\\s*[,;\\s]\\s*' + NUMBER + '\\s*$');

    function signed(number, hemisphere) {
        var value = parseFloat(number);
        return hemisphere && 'SsWw'.indexOf(hemisphere) !== -1 ? -value : value;
    }

    function parse(text) {
        var match = COORDINATES.exec(text || '');
        if (!match) {
            return null;
        }
        var latitude = signed(match[1], match[2]);
        var longitude = signed(match[3], match[4]);
        // A hemisphere letter can put the pair in either order; without one, latitude first.
        if (match[2] && 'EeWw'.indexOf(match[2]) !== -1) {
            var swap = latitude;
            latitude = longitude;
            longitude = swap;
        }
        if (Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
            return null;
        }
        return [latitude, longitude];
    }

    function flash(widget, state) {
        var button = widget.querySelector('.point-widget-paste');
        if (!button) {
            return;
        }
        button.classList.add(state);
        window.setTimeout(function () {
            button.classList.remove(state);
        }, 1200);
    }

    function fill(widget, text) {
        var coordinates = parse(text);
        var inputs = widget.querySelectorAll('input');
        if (!coordinates || inputs.length < 2) {
            flash(widget, 'point-widget-failed');
            return false;
        }
        coordinates.forEach(function (value, index) {
            inputs[index].value = value;
            inputs[index].dispatchEvent(new Event('input', {bubbles: true}));
            inputs[index].dispatchEvent(new Event('change', {bubbles: true}));
        });
        flash(widget, 'point-widget-ok');
        return true;
    }

    function widgetOf(target) {
        return target && target.closest ? target.closest('.point-widget') : null;
    }

    // Ctrl+V into either input: a paste event carries its own data, so this needs no
    // clipboard permission and shows no dialog anywhere. Text that isn't a coordinate pair
    // is left to the browser to handle normally.
    document.addEventListener('paste', function (event) {
        var widget = widgetOf(event.target);
        if (!widget || !event.target.matches('input')) {
            return;
        }
        var clipboard = event.clipboardData || window.clipboardData;
        var text = clipboard ? clipboard.getData('text') : '';
        if (parse(text) && fill(widget, text)) {
            event.preventDefault();
        }
    });

    // Reading the clipboard needs a secure context and the user's permission; when either
    // is missing, ask for the coordinates directly instead of failing silently.
    function prompted(widget) {
        var text = window.prompt('Paste coordinates (latitude, longitude):', '');
        if (text !== null) {
            fill(widget, text);
        }
    }

    // The button is the same thing for people who would rather click than paste.
    document.addEventListener('click', function (event) {
        var button = event.target.closest('.point-widget-paste');
        if (!button) {
            return;
        }
        event.preventDefault();
        var widget = widgetOf(button);
        if (navigator.clipboard && navigator.clipboard.readText) {
            navigator.clipboard.readText().then(
                function (text) { fill(widget, text); },
                function () { prompted(widget); }
            );
        } else {
            prompted(widget);
        }
    });
})();
