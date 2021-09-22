# # clear existing user defined variables
# for element in dir():
#     if element[0:2] != "__":
#         del globals()[element]

import os
import argparse
import pickle

import numpy as np
from scipy.fftpack import dct
from numpy_ringbuffer import RingBuffer
import pyaudio

import tensorflow as tf
from functions_online import OverlapHandler, load_model_layers, open_mic, get_sample

import matplotlib.pyplot as plt
import matplotlib.animation as animation


# ==============================================================
# ==============================================================
# KEYWORD SPOTTING - ONLINE IMPLEMENTATION
# ==============================================================
# ==============================================================

#python kws_realtime.py -r result_model02

parser = argparse.ArgumentParser()
parser.add_argument("-d","--datafolder",default='data',type=str,help="Path to data folder - generated by `prepare_dataset.py`.")
parser.add_argument("-r","--resultfolder",default='',type=str,help="Path to result folder.")

parser.add_argument("--frames_per_stft",default=7,type=int,help="Number of frames at each STFT.")
parser.add_argument("--samples_in_window",default=30,type=int,help="Number of output instants shown in the window.")
args = parser.parse_args()


CHANNELS = 1 #microphone audio channels
FORMAT = pyaudio.paInt16 #conversion format for PyAudio stream


def main():

    # --------------------------------------------
    #   LOAD  PARAMETERS  AND  MODEL
    # --------------------------------------------

    with open(os.path.join(args.resultfolder,"parameters.pickle"), 'rb') as f:
        train_params = pickle.load(f)
    with open(os.path.join(args.datafolder,"parameters.pickle"), 'rb') as f:
        dataset_params = pickle.load(f)

    keywords = dataset_params['keywords']
    sampling_rate = dataset_params['sampling_rate']

    # STFT parameters
    frame_length = train_params['frame_length']
    frame_step = train_params['frame_step']
    fft_length = train_params['fft_length']
    # MFCC parameters
    lower_freq = train_params['lower_freq']
    upper_freq = train_params['upper_freq']
    n_mel_bins = train_params['n_mel_bins']
    n_mfcc_bins = train_params['n_mfcc_bins']

    # other parameters
    stft_size = frame_length + (args.frames_per_stft-1) * frame_step
    stream_chunk = stft_size + frame_step - frame_length

    print('STFT size:', stft_size)
    print('Samples per stream:', stream_chunk)

    # load model layers (buffers and tensorflow LSTM)
    init_batch_norm, conv_layer_1, conv_layer_2, conv_layer_3, rec_layers = \
        load_model_layers(os.path.join(args.resultfolder,'model_weights.h5'))

    # constants for feature extraction
    specgram_feats = fft_length//2+1
    # mel-filter bank matrix
    mel_mat = tf.signal.linear_to_mel_weight_matrix(
        n_mel_bins, specgram_feats, sampling_rate, lower_freq, upper_freq)

    overlap = OverlapHandler(stft_size, frame_length-frame_step)

    # --------------------------------------------
    #   INITIALIZE RING BUFFERS
    # --------------------------------------------

    # data shown in the window
    pred_data = RingBuffer(capacity=args.samples_in_window, dtype=(np.float32,10))
    for i in range(args.samples_in_window):
        pred_data.append(np.zeros(10,dtype=np.float32))

    # --------------------------------------------
    #   READ FIRST SAMPLE
    # --------------------------------------------

    fig = plt.figure()
    ax = fig.add_subplot(111)

    # Launch the stream and the original spectrogram
    stream, pa = open_mic(FORMAT, CHANNELS, sampling_rate, stream_chunk)

    # put data in ring buffer
    overlap.insert(get_sample(stream, pa, stream_chunk))
    spectro = tf.signal.stft(overlap.get(), frame_length=frame_length, frame_step=frame_step, fft_length=fft_length)
    mel_spectro = tf.tensordot( tf.abs(spectro), mel_mat, 1)
    mfcc = tf.signal.mfccs_from_log_mel_spectrograms(tf.math.log(mel_spectro + 1e-8))[:,:n_mfcc_bins]

    # loop for each input data frame
    for i in range(args.frames_per_stft):
        conv_layer_1.insert(init_batch_norm(mfcc[i].numpy()))
        if conv_layer_1.pool_out:
            conv_layer_2.insert(conv_layer_1.get())
            if conv_layer_2.pool_out:
                conv_layer_3.insert(conv_layer_2.get())
                if conv_layer_3.pool_out:
                    soft = rec_layers(
                        np.expand_dims(np.expand_dims(conv_layer_3.get(), axis=0), axis=0),
                        training=False)
                    pred_data.append(soft[0,0,:])

    im = plt.imshow(np.array(pred_data).transpose(), aspect='auto', interpolation="none", cmap='Reds')


    ax.yaxis.tick_right()
    ax.set_yticks([0.,1.,2.,3.,4.,5.,6.,7.,8.,9.])
    ax.set_yticklabels(['(unknown)'] + keywords + ['(null)'])
    plt.xlabel('Output frames')
    plt.title('Keyword spotting - output probabilities')
    plt.subplots_adjust(bottom=0.12, top=0.9, left=0.05, right=0.85)

    # --------------------------------------------
    #   UPDATE FUNCTION AND ANIMATION
    # --------------------------------------------

    def update_fig(n):

        overlap.insert(get_sample(stream, pa, stream_chunk))
        spectro = tf.signal.stft(overlap.get(), frame_length=frame_length, frame_step=frame_step, fft_length=fft_length)
        mel_spectro = tf.tensordot( tf.abs(spectro), mel_mat, 1)
        mfcc = tf.signal.mfccs_from_log_mel_spectrograms(tf.math.log(mel_spectro + 1e-8))[:,:n_mfcc_bins]

        # loop for each input data frame
        for i in range(args.frames_per_stft):
            conv_layer_1.insert(init_batch_norm(mfcc[i].numpy()))
            if conv_layer_1.pool_out:
                conv_layer_2.insert(conv_layer_1.get())
                if conv_layer_2.pool_out:
                    conv_layer_3.insert(conv_layer_2.get())
                    if conv_layer_3.pool_out:
                        soft = rec_layers(
                            np.expand_dims(np.expand_dims(conv_layer_3.get(), axis=0), axis=0),
                            training=False)
                        pred_data.append(soft[0,0,:])
        im.set_array(np.array(pred_data).transpose())

        return im,

    ############### Animate ###############
    anim = animation.FuncAnimation(fig, update_fig, blit=False,
                                interval=stft_size/1000)

    try:
        plt.show()
    except:
        print("Plot Closed")

    stream.stop_stream()
    stream.close()
    pa.terminate()
    print("Program Terminated")



if __name__ == "__main__":
    main()
