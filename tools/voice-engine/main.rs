// Built against Kyutai Pocket TTS v4.0.1; see scripts/build-voice-engine.ps1.
mod audio;
mod config;
mod defaults;
mod model;
mod rng;
mod text;
mod tokenizer;
mod weights;

use anyhow::{ensure, Result};
use std::io::{self, BufRead, Write};
use std::sync::{Arc, atomic::{AtomicBool, Ordering}, mpsc};
use std::thread;

// The upstream loader calls this hook for weights, tokenizers and voices.
mod download {
    use anyhow::{ensure, Result};
    use std::path::{Path, PathBuf};

    pub fn download_if_necessary(name: &str) -> Result<PathBuf> {
        let path = Path::new(name).canonicalize()?;
        ensure!(path.starts_with(std::env::current_dir()?.canonicalize()?), "outside model bundle");
        Ok(path)
    }
}

fn packet(tag: u8, payload: &[u8]) -> io::Result<()> {
    let mut out = io::stdout().lock();
    out.write_all(&[tag])?;
    out.write_all(&(payload.len() as u32).to_le_bytes())?;
    out.write_all(payload)?;
    out.flush()
}

fn main() -> Result<()> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    ensure!(args.len() == 3, "expected config, voice, temperature or export-voice, reference, destination");
    xn::set_num_threads(thread::available_parallelism().map_or(1, usize::from).min(4));
    if args[0] == "export-voice" {
        let (cfg, origin) = config::Config::load(None, Some("cloning.yaml"))?;
        let engine = model::TtsModel::<xn::Unquantized<f32, xn::CpuDevice>>::load(cfg, origin, &Default::default())?;
        let voice = engine.get_state_for_audio_prompt(&args[1], true)?;
        return voice.export(std::path::Path::new(&args[2]));
    }
    let (cfg, origin) = config::Config::load(None, Some(&args[0]))?;
    let options = model::LoadOptions {
        temperature: Some(args[2].parse()?),
        ..Default::default()
    };
    let engine = model::TtsModel::<xn::Unquantized<f32, xn::CpuDevice>>::load(cfg, origin, &options)?;
    let voice = engine.get_state_for_audio_prompt(&args[1], false)?;
    let (tx, rx) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut cancel = Arc::new(AtomicBool::new(false));
        for result in io::stdin().lock().lines() {
            let Ok(line) = result else { break };
            if line.is_empty() {
                cancel.store(true, Ordering::Release);
                continue;
            }
            let Ok(text) = serde_json::from_str::<String>(&line) else { break };
            cancel = Arc::new(AtomicBool::new(false));
            if tx.send((text, cancel.clone())).is_err() { break; }
        }
        cancel.store(true, Ordering::Release);
    });
    packet(b'R', &(engine.sample_rate() as u32).to_le_bytes())?;
    for (text, cancel) in rx {
        let mut pcm = Vec::new();
        engine.generate_audio_stream(&voice, &text, defaults::MAX_TOKEN_PER_CHUNK, None,
            &cancel, &mut |samples| {
                ensure!(pcm.len() + samples.len() * 4 <= 128 * 1024 * 1024, "audio limit");
                pcm.extend(samples.iter().flat_map(|v| v.to_le_bytes()));
                Ok(())
            })?;
        if cancel.load(Ordering::Acquire) { packet(b'C', &[])?; }
        else { packet(b'A', &pcm)?; }
    }
    Ok(())
}
