#![forbid(unsafe_code)]

use crystal_core::{Generation, TileKey, TrustPolicy, Variant, WARNING, hash_hex};
use serde_json::json;
use std::{env, fs::OpenOptions, io::Write, process::ExitCode};

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args: Vec<_> = env::args().skip(1).collect();
    let development = args.iter().any(|a| a == "--allow-unsigned-development");
    args.retain(|a| a != "--allow-unsigned-development");
    if args.len() < 2
        || !["verify", "inspect", "read-tile", "decode-tile"].contains(&args[0].as_str())
    {
        return Err("usage: crystal-core verify|inspect PACKAGE [--allow-unsigned-development]\n       crystal-core read-tile|decode-tile PACKAGE before|after ZOOM X Y OUTPUT [--allow-unsigned-development]".into());
    }
    let policy = if development {
        TrustPolicy::DevelopmentUnsigned
    } else {
        TrustPolicy::default()
    };
    let generation = Generation::open(&args[1], policy)?;
    match args[0].as_str() {
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
