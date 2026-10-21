/* Send mono samples; leave output silent to prevent microphone feedback. */
class VoiceCapture extends AudioWorkletProcessor {
  process(inputs) {
    const input = inputs[0]?.[0];
    if (input) this.port.postMessage(input.slice());
    return true;
  }
}
registerProcessor("voice-capture", VoiceCapture);
