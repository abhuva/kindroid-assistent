{
    // x is already padded to whole codec frames. Every convolution keeps its
    // streaming state across blocks, including the left context at each boundary.
    let samples = x.dim(2usize)?;
    let block = 8 * self.frame_size;
    let mut transformer = self.encoder_transformer.init_state(x.dim(0usize)?, 8192)?;
    let mut encoded = Vec::new();
    for start in (0..samples).step_by(block) {
        let input = x.narrow(2, start..(start + block).min(samples))?.contiguous()?;
        let features = self.encoder.forward(&input, &mut enc_state)?;
        let mut outputs = self.encoder_transformer.forward(&features, &mut transformer)?;
        encoded.push(outputs.swap_remove(0));
    }
    Tensor::cat(&encoded.iter().collect::<Vec<_>>(), 2)?
}
