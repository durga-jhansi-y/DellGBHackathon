//! Read-only TX003 development-format verifier. No production signing support.
#![forbid(unsafe_code)]

use image::{ImageDecoder, ImageFormat, ImageReader, Limits};
use rusqlite::{Connection, MAIN_DB, limits::Limit};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs::{self, File},
    io::{self, Cursor, Read},
    os::unix::fs::FileExt,
    path::{Path, PathBuf},
};
use thiserror::Error;

pub const DATASET_ID: &str = "6971d9bc-6c17-5edc-bfc4-725e35642141";
pub const WARNING: &str = "UNSIGNED DEVELOPMENT ONLY; MUST NEVER BE ACCEPTED IN PRODUCTION";
const CONTAINER_ID: &str = "container-000001";
const CONTAINER_PATH: &str = "containers/container-000001.bin";
const MAX_INDEX: u64 = 1024 * 1024;
const MAX_CONTAINER: u64 = 16 * 1024 * 1024;
const MAX_PAYLOAD: u64 = 1024 * 1024;
const HEADER_SIZE: u64 = 256;
const SCHEMA: &str = include_str!("../../../schemas/index.sql");
const PROVENANCE: &str = include_str!("../../../crystal_geo/tx003.json");

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Error)]
pub enum Error {
    #[error("UnsignedGenerationRejected: explicit development opt-in required")]
    UnsignedGenerationRejected,
    #[error("ManifestInvalid: {0}")]
    ManifestInvalid(String),
    #[error("UnsafePath: {0}")]
    UnsafePath(String),
    #[error("IndexInvalid: {0}")]
    IndexInvalid(String),
    #[error("GenerationChanged: request or container does not match this generation")]
    GenerationChanged,
    #[error("PayloadOutOfBounds")]
    PayloadOutOfBounds,
    #[error("PayloadHashMismatch")]
    PayloadHashMismatch,
    #[error("FileHashMismatch: {0}")]
    FileHashMismatch(String),
    #[error("CodecUnsupported")]
    CodecUnsupported,
    #[error("DecodeLimitExceeded")]
    DecodeLimitExceeded,
    #[error("TileNotFound")]
    TileNotFound,
    #[error("StorageError: {0}")]
    Io(#[from] io::Error),
    #[error("IndexInvalid: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("ManifestInvalid: {0}")]
    Json(#[from] serde_json::Error),
    #[error("DecodeFailed: {0}")]
    Image(#[from] image::ImageError),
}

impl Error {
    pub fn code(&self) -> &'static str {
        match self {
            Self::UnsignedGenerationRejected => "UnsignedGenerationRejected",
            Self::ManifestInvalid(_) | Self::Json(_) => "ManifestInvalid",
            Self::UnsafePath(_) => "UnsafePath",
            Self::IndexInvalid(_) | Self::Sqlite(_) => "IndexInvalid",
            Self::GenerationChanged => "GenerationChanged",
            Self::PayloadOutOfBounds => "PayloadOutOfBounds",
            Self::PayloadHashMismatch => "PayloadHashMismatch",
            Self::FileHashMismatch(_) => "FileHashMismatch",
            Self::CodecUnsupported => "CodecUnsupported",
            Self::DecodeLimitExceeded => "DecodeLimitExceeded",
            Self::TileNotFound => "TileNotFound",
            Self::Io(_) => "StorageError",
            Self::Image(_) => "DecodeFailed",
        }
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub enum TrustPolicy {
    #[default]
    Production,
    DevelopmentUnsigned,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Variant {
    Before,
    After,
}

impl Variant {
    pub fn parse(value: &str) -> Result<Self> {
        match value {
            "before" => Ok(Self::Before),
            "after" => Ok(Self::After),
            _ => Err(Error::TileNotFound),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct TileKey {
    pub variant: Variant,
    pub zoom: u8,
    pub x: u32,
    pub y: u32,
}

#[derive(Clone, Debug, Serialize)]
pub struct TileDescriptor {
    pub generation_id: String,
    pub key: TileKey,
    pub bounds: [f64; 4],
    pub source_image: String,
    pub acquisition_date: String,
    offset: u64,
    length: u64,
    stored_sha256: [u8; 32],
}

/// Only the verified reader can construct this type. Fields cannot be mutated.
pub struct VerifiedCompressedTile {
    descriptor: TileDescriptor,
    bytes: Vec<u8>,
}

impl VerifiedCompressedTile {
    pub fn descriptor(&self) -> &TileDescriptor {
        &self.descriptor
    }

    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn decode(&self) -> Result<DecodedTile> {
        Ok(DecodedTile {
            descriptor: self.descriptor.clone(),
            rgba: decode_webp(&self.bytes)?,
            width: 256,
            height: 256,
        })
    }
}

pub struct DecodedTile {
    pub descriptor: TileDescriptor,
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

/// Owns the container descriptor and verified in-memory tile descriptors.
/// Directory renames or activation of another generation cannot change the handle.
pub struct Generation {
    generation_id: String,
    manifest_sha256: [u8; 32],
    container: File,
    container_size: u64,
    tiles: BTreeMap<TileKey, TileDescriptor>,
}

impl Generation {
    pub fn open(root: impl AsRef<Path>, policy: TrustPolicy) -> Result<Self> {
        if !matches!(policy, TrustPolicy::DevelopmentUnsigned) {
            return Err(Error::UnsignedGenerationRejected);
        }
        let root = root.as_ref();
        let raw = read_bounded(safe_file(root, "manifest.json")?, 65536)?;
        let value: Value = serde_json::from_slice(&raw)?;
        // Serializing a parsed duplicate-key object removes the duplicate, so it
        // cannot equal the canonical input. Serde's default nesting limit remains on.
        let mut normalized = serde_json::to_vec(&value)?;
        normalized.push(b'\n');
        manifest_check(raw == normalized, "noncanonical JSON or duplicate keys")?;
        let generation_id = validate_manifest(&value)?;
        let index: FileRecord = serde_json::from_value(value["index"].clone())?;
        let containers: Vec<ContainerRecord> = serde_json::from_value(value["containers"].clone())?;
        manifest_check(containers.len() == 1, "expected one container")?;
        let container_record = &containers[0];
        manifest_check(container_record.id == CONTAINER_ID, "container ID mismatch")?;
        let index_bytes = verify_record(root, &index, "index.sqlite", MAX_INDEX)?;
        let container_path = safe_file(root, &container_record.file.path)?;
        manifest_check(
            container_record.file.path == CONTAINER_PATH,
            "unexpected container path",
        )?;
        let mut container = File::open(container_path)?;
        check_file_size(&container, container_record.file.size, MAX_CONTAINER)?;
        if hash_stream(&mut container)? != parse_hash(&container_record.file.sha256)? {
            return Err(Error::FileHashMismatch(CONTAINER_PATH.into()));
        }
        check_directory(root, &["containers", "index.sqlite", "manifest.json"])?;
        check_directory(&root.join("containers"), &["container-000001.bin"])?;
        let mut actual_header = [0; HEADER_SIZE as usize];
        container.read_exact_at(&mut actual_header, 0)?;
        if actual_header != container_header(&generation_id)? {
            return Err(Error::GenerationChanged);
        }
        let tiles = read_index(&index_bytes, &generation_id, container_record.file.size)?;
        let result = Self {
            generation_id,
            manifest_sha256: hash_bytes(&raw),
            container,
            container_size: container_record.file.size,
            tiles,
        };
        // Match the Python activation verifier: reject invalid compressed tiles,
        // not just valid hashes. Interactive reads still authenticate each payload.
        for key in result.tiles.keys() {
            result
                .read_verified_tile(result.generation_id(), *key)?
                .decode()?;
        }
        Ok(result)
    }

    pub fn generation_id(&self) -> &str {
        &self.generation_id
    }

    pub fn manifest_sha256(&self) -> String {
        self.manifest_sha256
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }

    pub fn descriptors(&self) -> impl Iterator<Item = &TileDescriptor> {
        self.tiles.values()
    }

    pub fn read_verified_tile(
        &self,
        generation_id: &str,
        key: TileKey,
    ) -> Result<VerifiedCompressedTile> {
        if generation_id != self.generation_id {
            return Err(Error::GenerationChanged);
        }
        let descriptor = self.tiles.get(&key).ok_or(Error::TileNotFound)?;
        if self.container.metadata()?.len() != self.container_size {
            return Err(Error::PayloadOutOfBounds);
        }
        let mut actual_header = [0; HEADER_SIZE as usize];
        self.container.read_exact_at(&mut actual_header, 0)?;
        if actual_header != container_header(&self.generation_id)? {
            return Err(Error::GenerationChanged);
        }
        checked_range(descriptor.offset, descriptor.length, self.container_size)?;
        let length = usize::try_from(descriptor.length).map_err(|_| Error::PayloadOutOfBounds)?;
        let mut bytes = vec![0; length];
        self.container
            .read_exact_at(&mut bytes, descriptor.offset)?;
        if hash_bytes(&bytes) != descriptor.stored_sha256 {
            return Err(Error::PayloadHashMismatch);
        }
        Ok(VerifiedCompressedTile {
            descriptor: descriptor.clone(),
            bytes,
        })
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct FileRecord {
    path: String,
    size: u64,
    sha256: String,
}

// Flatten cannot be combined with deny_unknown_fields; parse the container
// record explicitly and convert to the same validated file-record contract.
struct ContainerRecord {
    id: String,
    file: FileRecord,
}

impl<'de> Deserialize<'de> for ContainerRecord {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        #[derive(Deserialize)]
        #[serde(deny_unknown_fields)]
        struct Record {
            id: String,
            path: String,
            size: u64,
            sha256: String,
        }
        let row = Record::deserialize(deserializer)?;
        Ok(Self {
            id: row.id,
            file: FileRecord {
                path: row.path,
                size: row.size,
                sha256: row.sha256,
            },
        })
    }
}

fn manifest_check(condition: bool, message: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(Error::ManifestInvalid(message.into()))
    }
}

fn index_check(condition: bool, message: &str) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(Error::IndexInvalid(message.into()))
    }
}

fn generation_id_valid(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id.as_bytes()[0].is_ascii_alphanumeric()
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

fn codec() -> Value {
    json!({"profile":"analysis-webp-lossless-v1", "lossless":true, "quality":100,
           "method":6, "exact":true, "pixel_format":"RGBA"})
}

fn validate_manifest(value: &Value) -> Result<String> {
    let mut actual = value
        .as_object()
        .ok_or_else(|| Error::ManifestInvalid("expected object".into()))?
        .clone();
    let generation_id = value["generation_id"]
        .as_str()
        .ok_or_else(|| Error::ManifestInvalid("generation ID missing".into()))?;
    manifest_check(
        generation_id_valid(generation_id),
        "unsafe generation identifier",
    )?;
    let tools = value["toolchain"]
        .as_object()
        .ok_or_else(|| Error::ManifestInvalid("toolchain missing".into()))?;
    manifest_check(
        tools.len() == 3
            && ["pillow", "webp", "sqlite"].iter().all(|key| {
                tools
                    .get(*key)
                    .and_then(Value::as_str)
                    .is_some_and(|v| !v.is_empty() && v.len() <= 64 && v.is_ascii())
            }),
        "invalid toolchain",
    )?;
    for key in ["toolchain", "index", "containers"] {
        manifest_check(actual.remove(key).is_some(), "missing required field")?;
    }
    let provenance: Value = serde_json::from_str(PROVENANCE)?;
    let expected = json!({
        "format":"crystal-geo-unsigned-dev-v1", "format_version":1,
        "dataset_id":DATASET_ID, "generation_id":generation_id,
        "signature_status":"unsigned-development", "production_acceptable":false,
        "warning":WARNING, "purpose":"development and noncommercial demonstration",
        "coverage":{"crs":"EPSG:4326", "bounds":[-95.625,29.84064389983442,-95.6195068359375,29.84540862642843], "grid_crs":"EPSG:3857"},
        "tile_count":32, "tile_size":256, "zoom":18,
        "codec_parameters":codec(), "provenance":provenance
    });
    manifest_check(
        Value::Object(actual) == expected,
        "unsupported development manifest fields",
    )?;
    Ok(generation_id.into())
}

fn safe_file(root: &Path, relative: &str) -> Result<PathBuf> {
    if relative.is_empty()
        || relative.len() > 128
        || !root.is_dir()
        || root.symlink_metadata()?.file_type().is_symlink()
    {
        return Err(Error::UnsafePath(relative.into()));
    }
    let mut path = root.to_path_buf();
    for component in relative.split('/') {
        if component.is_empty()
            || component == "."
            || component == ".."
            || !component
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            return Err(Error::UnsafePath(relative.into()));
        }
        path.push(component);
        if path.symlink_metadata()?.file_type().is_symlink() {
            return Err(Error::UnsafePath(relative.into()));
        }
    }
    if !path.is_file() {
        return Err(Error::UnsafePath(relative.into()));
    }
    Ok(path)
}

fn check_directory(root: &Path, expected: &[&str]) -> Result<()> {
    let mut names: Vec<_> = fs::read_dir(root)?
        .map(|entry| entry.map(|e| e.file_name()))
        .collect::<io::Result<_>>()?;
    names.sort();
    let mut wanted: Vec<_> = expected.iter().map(std::ffi::OsString::from).collect();
    wanted.sort();
    manifest_check(names == wanted, "unexpected package files")
}

fn read_bounded(path: PathBuf, limit: u64) -> Result<Vec<u8>> {
    let file = File::open(path)?;
    manifest_check(file.metadata()?.len() <= limit, "file exceeds limit")?;
    let mut result = Vec::new();
    file.take(limit + 1).read_to_end(&mut result)?;
    manifest_check(result.len() as u64 <= limit, "file exceeds limit")?;
    Ok(result)
}

fn check_file_size(file: &File, size: u64, limit: u64) -> Result<()> {
    manifest_check(
        size > 0 && size <= limit && file.metadata()?.len() == size,
        "file size mismatch",
    )
}

fn verify_record(root: &Path, record: &FileRecord, expected: &str, limit: u64) -> Result<Vec<u8>> {
    let path = safe_file(root, &record.path)?;
    manifest_check(record.path == expected, "unexpected package path")?;
    let bytes = read_bounded(path, limit)?;
    manifest_check(
        record.size > 0 && record.size == bytes.len() as u64,
        "file size mismatch",
    )?;
    if hash_bytes(&bytes) != parse_hash(&record.sha256)? {
        return Err(Error::FileHashMismatch(expected.into()));
    }
    Ok(bytes)
}

pub fn hash_bytes(bytes: &[u8]) -> [u8; 32] {
    Sha256::digest(bytes).into()
}

pub fn hash_hex(bytes: &[u8]) -> String {
    hash_bytes(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn parse_hash(value: &str) -> Result<[u8; 32]> {
    manifest_check(
        value.len() == 64
            && value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
        "invalid SHA-256",
    )?;
    let mut hash = [0; 32];
    for (i, byte) in hash.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&value[2 * i..2 * i + 2], 16)
            .map_err(|_| Error::ManifestInvalid("invalid SHA-256".into()))?;
    }
    Ok(hash)
}

fn hash_stream(file: &mut File) -> Result<[u8; 32]> {
    let mut hasher = Sha256::new();
    let mut total = 0_u64;
    let mut buffer = [0; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(count as u64)
            .ok_or(Error::PayloadOutOfBounds)?;
        manifest_check(total <= MAX_CONTAINER, "container exceeds limit")?;
        hasher.update(&buffer[..count]);
    }
    Ok(hasher.finalize().into())
}

fn container_header(id: &str) -> Result<[u8; 256]> {
    manifest_check(generation_id_valid(id), "unsafe generation identifier")?;
    let mut bytes = [0; 256];
    bytes[..8].copy_from_slice(b"CGEOdev1");
    bytes[8..12].copy_from_slice(&1_u32.to_be_bytes());
    bytes[12..16].copy_from_slice(&256_u32.to_be_bytes());
    bytes[16..16 + id.len()].copy_from_slice(id.as_bytes());
    bytes[80..80 + CONTAINER_ID.len()].copy_from_slice(CONTAINER_ID.as_bytes());
    let hash = hash_bytes(&bytes[..224]);
    bytes[224..].copy_from_slice(&hash);
    Ok(bytes)
}

fn checked_range(offset: u64, length: u64, size: u64) -> Result<u64> {
    let end = offset
        .checked_add(length)
        .ok_or(Error::PayloadOutOfBounds)?;
    if offset < HEADER_SIZE || length == 0 || length > MAX_PAYLOAD || end > size {
        return Err(Error::PayloadOutOfBounds);
    }
    Ok(end)
}

fn tile_bounds(x: u32, y: u32) -> [f64; 4] {
    let longitude = |x: u32| f64::from(x) / 262144.0 * 360.0 - 180.0;
    let latitude = |y: u32| {
        (std::f64::consts::PI * (1.0 - 2.0 * f64::from(y) / 262144.0))
            .sinh()
            .atan()
            .to_degrees()
    };
    [longitude(x), latitude(y + 1), longitude(x + 1), latitude(y)]
}

fn schema_signature(db: &Connection) -> Result<Vec<(String, String, String, String)>> {
    let mut statement =
        db.prepare("SELECT type,name,tbl_name,coalesce(sql,'') FROM sqlite_master ORDER BY name")?;
    Ok(statement
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))?
        .collect::<rusqlite::Result<_>>()?)
}

fn read_index(
    bytes: &[u8],
    generation: &str,
    size: u64,
) -> Result<BTreeMap<TileKey, TileDescriptor>> {
    let mut db = Connection::open_in_memory()?;
    // The reader is an exact-length immutable slice, so the binding's read cannot
    // fail halfway through or consume bytes outside the authenticated snapshot.
    db.deserialize_read_exact(MAIN_DB, bytes, bytes.len(), true)?;
    db.execute_batch("PRAGMA query_only=ON; PRAGMA trusted_schema=OFF;")?;
    db.set_limit(Limit::SQLITE_LIMIT_LENGTH, MAX_INDEX as i32)?;
    db.set_limit(Limit::SQLITE_LIMIT_ATTACHED, 0)?;
    let mut instructions = 0;
    db.progress_handler(
        100,
        Some(move || {
            instructions += 1;
            instructions > 20000
        }),
    );
    let expected_db = Connection::open_in_memory()?;
    expected_db.execute_batch(SCHEMA)?;
    index_check(
        schema_signature(&db)? == schema_signature(&expected_db)?,
        "schema mismatch",
    )?;
    index_check(
        db.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))? == 1,
        "schema version mismatch",
    )?;
    let integrity: Vec<String> = db
        .prepare("PRAGMA integrity_check")?
        .query_map([], |r| r.get(0))?
        .collect::<rusqlite::Result<_>>()?;
    index_check(integrity == ["ok"], "integrity failure")?;
    index_check(
        !db.prepare("PRAGMA foreign_key_check")?.exists([])?,
        "foreign key failure",
    )?;
    index_check(
        db.query_row("SELECT count(*) FROM generation_metadata", [], |r| {
            r.get::<_, i64>(0)
        })? == 1,
        "generation count",
    )?;
    let metadata: (String, String, i64, String, String, i64) =
        db.query_row("SELECT * FROM generation_metadata", [], |r| {
            Ok((
                r.get(0)?,
                r.get(1)?,
                r.get(2)?,
                r.get(3)?,
                r.get(4)?,
                r.get(5)?,
            ))
        })?;
    index_check(
        metadata
            == (
                DATASET_ID.into(),
                generation.into(),
                1,
                "EPSG:4326".into(),
                "EPSG:3857".into(),
                18,
            ),
        "generation mismatch",
    )?;
    validate_sources(&db)?;
    index_check(
        db.query_row("SELECT count(*) FROM tile_bounds", [], |r| {
            r.get::<_, i64>(0)
        })? == 32,
        "R-tree count mismatch",
    )?;
    let mut statement = db.prepare("SELECT * FROM tiles ORDER BY tile_id LIMIT 33")?;
    let mut rows = statement.query([])?;
    let provenance: Value = serde_json::from_str(PROVENANCE)?;
    let mut tiles = BTreeMap::new();
    let mut previous_end = HEADER_SIZE;
    let mut count = 0_usize;
    while let Some(row) = rows.next()? {
        index_check(count < 32, "too many tiles")?;
        let source = &provenance["sources"][count / 16];
        let key = TileKey {
            variant: if count < 16 {
                Variant::Before
            } else {
                Variant::After
            },
            zoom: 18,
            x: 61440 + (count % 4) as u32,
            y: 108284 + ((count % 16) / 4) as u32,
        };
        index_check(
            row.get::<_, i64>(0)? == count as i64 + 1
                && row.get::<_, String>(1)? == generation
                && row.get::<_, String>(2)? == source["image_id"].as_str().unwrap_or("")
                && row.get::<_, String>(3)? == source["variant"].as_str().unwrap_or("")
                && row.get::<_, u8>(4)? == key.zoom
                && row.get::<_, u32>(5)? == key.x
                && row.get::<_, u32>(6)? == key.y,
            "tile generation or coordinate mismatch",
        )?;
        let bounds: [f64; 4] = [row.get(7)?, row.get(8)?, row.get(9)?, row.get(10)?];
        index_check(
            bounds
                .iter()
                .zip(tile_bounds(key.x, key.y))
                .all(|(a, b)| a.is_finite() && (a - b).abs() <= 1e-12),
            "tile bounds mismatch",
        )?;
        let spatial: [f64; 4] = db.query_row(
            "SELECT west,south,east,north FROM tile_bounds WHERE tile_id=?",
            [count as i64 + 1],
            |r| Ok([r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?]),
        )?;
        index_check(
            spatial
                .iter()
                .zip(bounds)
                .all(|(a, b)| a.is_finite() && (a - b).abs() < 2e-5),
            "R-tree bounds mismatch",
        )?;
        index_check(
            row.get::<_, i64>(11)? == 256
                && row.get::<_, i64>(12)? == 256
                && row.get::<_, String>(13)? == CONTAINER_ID
                && row.get::<_, String>(16)? == "webp"
                && row.get::<_, String>(17)? == serde_json::to_string(&codec())?,
            "unsupported tile profile",
        )?;
        let offset =
            u64::try_from(row.get::<_, i64>(14)?).map_err(|_| Error::PayloadOutOfBounds)?;
        let length =
            u64::try_from(row.get::<_, i64>(15)?).map_err(|_| Error::PayloadOutOfBounds)?;
        let end = checked_range(offset, length, size)?;
        index_check(offset == previous_end, "overlap or unindexed bytes")?;
        previous_end = end;
        let stored_sha256: [u8; 32] = row
            .get::<_, Vec<u8>>(18)?
            .try_into()
            .map_err(|_| Error::IndexInvalid("invalid payload hash".into()))?;
        let image_id = source["image_id"]
            .as_str()
            .ok_or_else(|| Error::IndexInvalid("source missing".into()))?;
        tiles.insert(
            key,
            TileDescriptor {
                generation_id: generation.into(),
                key,
                bounds,
                source_image: format!("{image_id}.png"),
                acquisition_date: source["acquisition_date"].as_str().unwrap_or("").into(),
                offset,
                length,
                stored_sha256,
            },
        );
        count += 1;
    }
    index_check(
        count == 32 && previous_end == size,
        "incomplete payload coverage",
    )?;
    Ok(tiles)
}

fn validate_sources(db: &Connection) -> Result<()> {
    let provenance: Value = serde_json::from_str(PROVENANCE)?;
    let mut statement = db.prepare("SELECT * FROM sources ORDER BY variant DESC LIMIT 3")?;
    let mut rows = statement.query([])?;
    for source in provenance["sources"]
        .as_array()
        .ok_or_else(|| Error::IndexInvalid("sources missing".into()))?
    {
        let row = rows
            .next()?
            .ok_or_else(|| Error::IndexInvalid("missing source".into()))?;
        for (column, key) in [
            (0, "image_id"),
            (1, "variant"),
            (2, "acquisition_date"),
            (5, "source_agency"),
            (6, "attribution"),
            (7, "reuse"),
        ] {
            index_check(
                row.get::<_, String>(column)? == source[key].as_str().unwrap_or(""),
                "source metadata mismatch",
            )?;
        }
        index_check(
            row.get::<_, String>(3)?
                == format!("{}.png", source["image_id"].as_str().unwrap_or(""))
                && row.get::<_, Vec<u8>>(4)?
                    == parse_hash(source["sha256"].as_str().unwrap_or(""))?,
            "source image or hash mismatch",
        )?;
    }
    index_check(rows.next()?.is_none(), "extra sources")
}

fn decode_webp(bytes: &[u8]) -> Result<Vec<u8>> {
    if bytes.len() as u64 > MAX_PAYLOAD
        || bytes.get(..4) != Some(b"RIFF")
        || bytes.get(8..12) != Some(b"WEBP")
        || bytes.get(12..16) != Some(b"VP8L")
    {
        return Err(Error::CodecUnsupported);
    }
    let mut limits = Limits::default();
    limits.max_image_width = Some(256);
    limits.max_image_height = Some(256);
    limits.max_alloc = Some(8 * 1024 * 1024);
    let mut reader = ImageReader::with_format(Cursor::new(bytes), ImageFormat::WebP);
    reader.limits(limits);
    let decoder = reader.into_decoder()?;
    if decoder.dimensions() != (256, 256) || decoder.total_bytes() > 256 * 256 * 4 {
        return Err(Error::DecodeLimitExceeded);
    }
    let image = image::DynamicImage::from_decoder(decoder)?;
    Ok(image.into_rgba8().into_raw())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Seek, SeekFrom, Write};

    fn fixture() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../../testdata/tiny_valid_generation")
    }

    fn copied_fixture() -> Result<(tempfile::TempDir, PathBuf)> {
        let temporary = tempfile::tempdir()?;
        let root = temporary.path().join("generation");
        fs::create_dir(&root)?;
        fs::create_dir(root.join("containers"))?;
        for relative in ["manifest.json", "index.sqlite", CONTAINER_PATH] {
            fs::write(root.join(relative), fs::read(fixture().join(relative))?)?;
        }
        Ok((temporary, root))
    }

    #[test]
    fn post_open_corruption_header_changes_and_truncation_are_rejected() -> Result<()> {
        let (_temporary, root) = copied_fixture()?;
        let generation = Generation::open(&root, TrustPolicy::DevelopmentUnsigned)?;
        let key = TileKey {
            variant: Variant::Before,
            zoom: 18,
            x: 61440,
            y: 108284,
        };
        let mut writer = fs::OpenOptions::new()
            .write(true)
            .open(root.join(CONTAINER_PATH))?;
        writer.seek(SeekFrom::Start(HEADER_SIZE + 30))?;
        writer.write_all(b"changed")?;
        assert!(matches!(
            generation.read_verified_tile(generation.generation_id(), key),
            Err(Error::PayloadHashMismatch)
        ));
        writer.seek(SeekFrom::Start(0))?;
        writer.write_all(b"X")?;
        assert!(matches!(
            generation.read_verified_tile(generation.generation_id(), key),
            Err(Error::GenerationChanged)
        ));
        writer.set_len(HEADER_SIZE)?;
        assert!(matches!(
            generation.read_verified_tile(generation.generation_id(), key),
            Err(Error::PayloadOutOfBounds)
        ));
        Ok(())
    }

    #[test]
    fn container_descriptor_and_index_snapshot_remain_pinned() -> Result<()> {
        let (_temporary, root) = copied_fixture()?;
        let generation = Generation::open(&root, TrustPolicy::DevelopmentUnsigned)?;
        let key = TileKey {
            variant: Variant::Before,
            zoom: 18,
            x: 61440,
            y: 108284,
        };
        let expected = generation
            .read_verified_tile(generation.generation_id(), key)?
            .bytes()
            .to_vec();
        fs::rename(root.join(CONTAINER_PATH), root.join("retained-container"))?;
        fs::write(root.join(CONTAINER_PATH), b"another container")?;
        fs::write(root.join("index.sqlite"), b"another index")?;
        assert_eq!(
            generation
                .read_verified_tile(generation.generation_id(), key)?
                .bytes(),
            expected
        );
        assert!(Generation::open(&root, TrustPolicy::DevelopmentUnsigned).is_err());
        Ok(())
    }

    #[test]
    fn unsigned_is_rejected_by_default() {
        assert!(matches!(
            Generation::open(fixture(), TrustPolicy::default()),
            Err(Error::UnsignedGenerationRejected)
        ));
    }

    #[test]
    fn valid_generation_reads_every_tile() -> Result<()> {
        let generation = Generation::open(fixture(), TrustPolicy::DevelopmentUnsigned)?;
        assert_eq!(generation.descriptors().count(), 32);
        for descriptor in generation.descriptors() {
            let tile = generation.read_verified_tile(generation.generation_id(), descriptor.key)?;
            assert_eq!(hash_bytes(tile.bytes()), descriptor.stored_sha256);
            assert_eq!(tile.decode()?.rgba.len(), 256 * 256 * 4);
        }
        assert!(matches!(
            generation.read_verified_tile(
                "another-generation",
                TileKey {
                    variant: Variant::Before,
                    zoom: 18,
                    x: 61440,
                    y: 108284
                }
            ),
            Err(Error::GenerationChanged)
        ));
        assert!(matches!(
            generation.read_verified_tile(
                generation.generation_id(),
                TileKey {
                    variant: Variant::Before,
                    zoom: 18,
                    x: 0,
                    y: 0
                }
            ),
            Err(Error::TileNotFound)
        ));
        Ok(())
    }

    #[test]
    fn ranges_reject_overflow_and_invalid_lengths() {
        for (offset, length, size) in [
            (u64::MAX, 2, u64::MAX),
            (255, 1, 300),
            (256, 0, 300),
            (256, 45, 300),
            (256, MAX_PAYLOAD + 1, u64::MAX),
        ] {
            assert!(matches!(
                checked_range(offset, length, size),
                Err(Error::PayloadOutOfBounds)
            ));
        }
        assert_eq!(checked_range(256, 44, 300).ok(), Some(300));
    }

    #[test]
    fn decoder_rejects_oversized_dimensions_before_allocating_pixels() {
        // Minimal lossless header declaring a 257x256 image; no entropy data needed.
        let packed = 256_u32 | (255_u32 << 14);
        let mut bytes = b"RIFF\x12\0\0\0WEBPVP8L\x05\0\0\0\x2f".to_vec();
        bytes.extend_from_slice(&packed.to_le_bytes());
        bytes.push(0);
        assert!(decode_webp(&bytes).is_err());
    }
}
