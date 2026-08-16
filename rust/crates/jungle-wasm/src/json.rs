//! Just enough JSON to speak the bridge protocol.
//!
//! The protocol has seven fixed response shapes carrying integers, booleans,
//! nulls and a closed set of ASCII strings (animal names, four reason words, and
//! formatted history lines). A serialisation framework would be more code in the
//! payload than the protocol is, so this hand-rolls it — and the one place real
//! parsing is needed, `replay_moves`, takes a single well-defined shape.

use core::fmt::Write;

/// Append `s` as a JSON string literal.
///
/// Nothing the engine emits currently needs escaping. It is done anyway because
/// "no caller passes a quote" is the kind of invariant that stops being true
/// silently, and a broken envelope surfaces as an unparseable reply in a Worker.
pub fn push_string(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `{"ok": true, "data": <data>, "error": null}`
pub fn ok(data: &str) -> String {
    format!("{{\"ok\":true,\"data\":{data},\"error\":null}}")
}

/// `{"ok": false, "data": null, "error": "<message>"}`
pub fn err(message: &str) -> String {
    let mut out = String::from("{\"ok\":false,\"data\":null,\"error\":");
    push_string(&mut out, message);
    out.push('}');
    out
}

/// Parse `[[fc,fr,tc,tr], ...]`, the sole input shape the bridge accepts.
///
/// Entries longer than four are truncated rather than rejected, matching the
/// `entry[:4]` the Python bridge did.
pub fn parse_move_array(text: &str) -> Result<Vec<[i32; 4]>, String> {
    let b = text.as_bytes();
    let mut i = 0usize;
    let mut out = Vec::new();

    let skip_ws = |i: &mut usize| {
        while *i < b.len() && (b[*i] as char).is_ascii_whitespace() {
            *i += 1
        }
    };

    skip_ws(&mut i);
    if i >= b.len() || b[i] != b'[' {
        return Err("expected a JSON array of moves".into());
    }
    i += 1;
    skip_ws(&mut i);
    if i < b.len() && b[i] == b']' {
        return Ok(out);
    }

    loop {
        skip_ws(&mut i);
        if i >= b.len() || b[i] != b'[' {
            return Err(format!("expected '[' at offset {i}"));
        }
        i += 1;

        let mut nums: Vec<i32> = Vec::new();
        loop {
            skip_ws(&mut i);
            if i < b.len() && b[i] == b']' {
                i += 1;
                break;
            }
            let start = i;
            if i < b.len() && (b[i] == b'-' || b[i] == b'+') {
                i += 1;
            }
            while i < b.len() && b[i].is_ascii_digit() {
                i += 1;
            }
            if i == start {
                return Err(format!("expected an integer at offset {i}"));
            }
            let n: i32 = text[start..i]
                .parse()
                .map_err(|_| format!("bad integer {:?}", &text[start..i]))?;
            nums.push(n);
            skip_ws(&mut i);
            if i < b.len() && b[i] == b',' {
                i += 1;
            }
        }

        if nums.len() < 4 {
            return Err(format!(
                "move {} has {} fields, need 4",
                out.len(),
                nums.len()
            ));
        }
        out.push([nums[0], nums[1], nums[2], nums[3]]);

        skip_ws(&mut i);
        if i < b.len() && b[i] == b',' {
            i += 1;
            continue;
        }
        if i < b.len() && b[i] == b']' {
            return Ok(out);
        }
        return Err(format!("expected ',' or ']' at offset {i}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelopes_have_the_documented_shape() {
        assert_eq!(
            ok("{\"a\":1}"),
            "{\"ok\":true,\"data\":{\"a\":1},\"error\":null}"
        );
        assert_eq!(
            err("boom"),
            "{\"ok\":false,\"data\":null,\"error\":\"boom\"}"
        );
    }

    #[test]
    fn error_messages_are_escaped() {
        assert_eq!(
            err("say \"hi\"\n"),
            r#"{"ok":false,"data":null,"error":"say \"hi\"\n"}"#
        );
    }

    #[test]
    fn move_arrays_parse() {
        assert_eq!(parse_move_array("[]").unwrap(), Vec::<[i32; 4]>::new());
        assert_eq!(parse_move_array("[[0,6,0,5]]").unwrap(), vec![[0, 6, 0, 5]]);
        assert_eq!(
            parse_move_array(" [ [0, 6, 0, 5] , [6,2,6,3] ] ").unwrap(),
            vec![[0, 6, 0, 5], [6, 2, 6, 3]]
        );
        // Extra fields are truncated, as the Python bridge's `entry[:4]` did.
        assert_eq!(
            parse_move_array("[[1,2,3,4,99]]").unwrap(),
            vec![[1, 2, 3, 4]]
        );
        assert_eq!(
            parse_move_array("[[-1,0,0,0]]").unwrap(),
            vec![[-1, 0, 0, 0]]
        );
    }

    #[test]
    fn malformed_move_arrays_are_rejected_not_guessed() {
        for bad in [
            "",
            "{}",
            "[[1,2,3]]",
            "[[1,2,3,4]",
            "[[1,2,3,x]]",
            "[1,2,3,4]",
        ] {
            assert!(parse_move_array(bad).is_err(), "should reject {bad:?}");
        }
    }
}
