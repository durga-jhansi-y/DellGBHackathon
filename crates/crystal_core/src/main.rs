#![forbid(unsafe_code)]

use base64::{Engine as _, engine::general_purpose::STANDARD};
use crystal_core::{Generation, TileKey, TrustPolicy, Variant, WARNING, hash_hex};
use serde::Deserialize;
use serde_json::json;
use std::{
    env,
    fs::OpenOptions,
    io::{self, BufRead, Read, Write},
    process::ExitCode,
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TileRequest {
    request_id: u64,
    generation_id: String,
    variant: Variant,
    zoom: u8,
    x: u32,
    y: u32,
}

fn serve(generation: &Generation) -> Result<(), Box<dyn std::error::Error>> {
    let mut output = io::stdout().lock();
    let mut input = io::stdin().lock();
    let hello = json!({"protocol_version":1,"generation_id":generation.generation_id(),
                      "manifest_sha256":generation.manifest_sha256(),"warning":WARNING,
                      "tiles":generation.descriptors().collect::<Vec<_>>()});
    serde_json::to_writer(&mut output, &hello)?;
    output.write_all(b"\n")?;
    output.flush()?;
    loop {
        let mut line = Vec::new();
        let count = (&mut input).take(4097).read_until(b'\n', &mut line)?;
        if count == 0 {
            break;
        }
        if count > 4096 || line.last() != Some(&b'\n') {
            return Err("ProtocolInvalid: oversized or incomplete request".into());
        }
        let response = match serde_json::from_slice::<TileRequest>(&line) {
            Ok(request) => {
                let key = TileKey {
                    variant: request.variant,
                    zoom: request.zoom,
                    x: request.x,
                    y: request.y,
                };
                match generation.read_verified_tile(&request.generation_id, key) {
                    Ok(tile) => json!({"request_id":request.request_id,"ok":true,
                                      "descriptor":tile.descriptor(),"payload_base64":STANDARD.encode(tile.bytes())}),
                    Err(error) => json!({"request_id":request.request_id,"ok":false,
                                        "error":{"code":error.code(),"message":error.to_string()}}),
                }
            }
            Err(_) => json!({"request_id":null,"ok":false,
                             "error":{"code":"ProtocolInvalid","message":"invalid tile request"}}),
        };
        serde_json::to_writer(&mut output, &response)?;
        output.write_all(b"\n")?;
        output.flush()?;
    }
    Ok(())
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args: Vec<_> = env::args().skip(1).collect();
    let development = args.iter().any(|a| a == "--allow-unsigned-development");
    args.retain(|a| a != "--allow-unsigned-development");
    if args.len() < 2
        || !["verify", "inspect", "read-tile", "decode-tile", "serve"].contains(&args[0].as_str())
    {
        return Err("usage: crystal-core verify|inspect|serve PACKAGE [--allow-unsigned-development]\n       crystal-core read-tile|decode-tile PACKAGE before|after ZOOM X Y OUTPUT [--allow-unsigned-development]".into());
    }
    let policy = if development {
        TrustPolicy::DevelopmentUnsigned
    } else {
        TrustPolicy::default()
    };
    let generation = Generation::open(&args[1], policy)?;
    match args[0].as_str() {
        "serve" if args.len() == 2 => serve(&generation)?,
        "verify" if args.len() == 2 => {
            println!(
                "VERIFIED: {}; 32 lossless WebP tiles; {WARNING}",
                generation.generation_id()
            );
        }
        "inspect" if args.len() == 2 => {
            let mut tiles = Vec::new();
            for descriptor in generation.descriptors() {
                let tile =
                    generation.read_verified_tile(generation.generation_id(), descriptor.key)?;
                tiles.push(
                    json!({"descriptor":descriptor,"stored_sha256":hash_hex(tile.bytes()),
                                  "decoded_rgba_sha256":hash_hex(&tile.decode()?.rgba)}),
                );
            }
            println!(
                "{}",
                json!({"generation_id":generation.generation_id(),"tiles":tiles,"warning":WARNING})
            );
        }
        "read-tile" | "decode-tile" if args.len() == 7 => {
            let key = TileKey {
                variant: Variant::parse(&args[2])?,
                zoom: args[3].parse()?,
                x: args[4].parse()?,
                y: args[5].parse()?,
            };
            let tile = generation.read_verified_tile(generation.generation_id(), key)?;
            let decoded;
            let bytes = if args[0] == "decode-tile" {
                decoded = tile.decode()?;
                &decoded.rgba[..]
            } else {
                tile.bytes()
            };
            let mut output = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&args[6])?;
            output.write_all(bytes)?;
            println!(
                "{}",
                json!({"generation_id":generation.generation_id(),"key":key,
                                "bytes":bytes.len(),"sha256":hash_hex(bytes),"output":args[6]})
            );
        }
        _ => return Err("invalid arguments".into()),
    }
    eprintln!("{WARNING}");
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("ERROR: {error}");
            ExitCode::FAILURE
        }
    }
}
