from config import ConfigArgs as args
import torch
import torch.nn as nn
import numpy as np


class Conv2d(nn.Conv2d):
    """
    Convolution 2d
    Args:
        x: (N, C_in, T)
    Returns:
        y: (N, C_out, T)
    """

    def __init__(self, in_channels, out_channels, kernel_size, activation_fn=None, drop_rate=0.,
                 stride=1, padding='same', dilation=1, groups=1, bias=True, bn=False):
        self.activation_fn = activation_fn
        self.drop_rate = drop_rate
        if padding == 'same':
            padding = kernel_size // 2 * dilation
            self.even_kernel = not bool(kernel_size % 2)
        super(Conv2d, self).__init__(in_channels, out_channels, kernel_size,
                                     stride=stride, padding=padding, dilation=dilation,
                                     groups=groups, bias=bias)
        self.drop_out = nn.Dropout(drop_rate) if drop_rate > 0 else None
        self.batch_norm = nn.BatchNorm2d(out_channels, eps=1e-3, momentum=0.001) if bn else None

    def forward(self, x):
        y = super(Conv2d, self).forward(x)
        y = self.batch_norm(y) if self.batch_norm is not None else y
        y = self.activation_fn(y) if self.activation_fn is not None else y
        y = self.drop_out(y) if self.drop_out is not None else y
        y = y[:, :, :-1] if self.even_kernel else y
        return y

class Conv1d(nn.Conv1d):
    """
    Convolution 1d
    Args:
        x: (N, C_in, T)
    Returns:
        y: (N, C_out, T)
    """

    def __init__(self, in_channels, out_channels, kernel_size, activation_fn=None, drop_rate=0.,
                 stride=1, padding='same', dilation=1, groups=1, bias=True, bn=False):
        self.activation_fn = activation_fn
        self.drop_rate = drop_rate
        if padding == 'same':
            padding = kernel_size // 2 * dilation
            self.even_kernel = not bool(kernel_size % 2)
        super(Conv1d, self).__init__(in_channels, out_channels, kernel_size,
                                            stride=stride, padding=padding, dilation=dilation,
                                            groups=groups, bias=bias)
        self.drop_out = nn.Dropout(drop_rate) if drop_rate > 0 else None
        self.batch_norm = nn.BatchNorm1d(out_channels, eps=1e-3, momentum=0.001) if bn else None

    def forward(self, x):
        y = super(Conv1d, self).forward(x)
        y = self.batch_norm(y) if self.batch_norm is not None else y
        y = self.activation_fn(y) if self.activation_fn is not None else y
        y = self.drop_out(y) if self.drop_out is not None else y
        y = y[:, :, :-1] if self.even_kernel else y
        return y

class Conv1dBank(nn.Module):
    """
    1D Convolution Bank
    Args:
        x: (N, C_in, T)
    Returns:
        y: (N, K*C_out, T)
    """

    def __init__(self, in_channels, out_channels, K, activation_fn=None):
        self.K = K
        super(Conv1dBank, self).__init__()
        self.conv_bank = nn.ModuleList([
            Conv1d(in_channels, out_channels, k, activation_fn=activation_fn, bias=False, bn=True)
            for k in range(1, self.K+1)
        ])

    def forward(self, x):
        convs = []
        for i in range(self.K):
            convs.append(self.conv_bank[i](x))
        y = torch.cat(convs, dim=1)
        return y

class Highway(nn.Linear):
    """
    Highway Network
    Args:
        x: (N, T, input_dim)
    Returns:
        y: (N, T, input_dim)
    """
    def __init__(self, input_dim, drop_rate=0.):
        self.drop_rate = drop_rate
        super(Highway, self).__init__(input_dim, input_dim*2)
        self.drop_out = nn.Dropout(self.drop_rate) if drop_rate > 0 else None

    def forward(self, x):
        y = super(Highway, self).forward(x) # (N, C_out*2, T)
        h, y_ = y.chunk(2, dim=-1) # half size for axis C_out. (N, C_out, T) respectively
        h = torch.sigmoid(h) # Gate
        y_ = torch.relu(y_)
        y_ = h*y_ + (1-h)*x
        y_ = self.drop_out(y_) if self.drop_out is not None else y_
        return y_

class HighwayConv1d(Conv1d):
    """
    Hightway Convolution 1d
    Args:
        x: (N, C_in, T)
    Returns:
        y: (N, C_out, T)
    """
    def __init__(self, in_channels, out_channels, kernel_size, drop_rate=0.,
                 stride=1, padding='same', dilation=1, groups=1, bias=True):
        self.drop_rate = drop_rate
        super(HighwayConv1d, self).__init__(in_channels, out_channels*2, kernel_size, activation_fn=None,
                                            stride=stride, padding=padding, dilation=dilation,
                                            groups=groups, bias=bias)
        self.drop_out = nn.Dropout(self.drop_rate) if drop_rate > 0 else None

    def forward(self, x):
        y = super(HighwayConv1d, self).forward(x) # (N, C_out*2, T)
        h, y_ = y.chunk(2, dim=1) # half size for axis C_out. (N, C_out, T) respectively
        h = torch.sigmoid(h) # Gate
        y_ = torch.relu(y_)
        y_ = h*y_ + (1-h)*x
        y_ = self.drop_out(y_) if self.drop_out is not None else y_
        return y_


class AttentionRNN(nn.Module):
    """
    Attention RNN
    Args:
        h: (N, Tx, Cx) Encoder outputs
        s: (N, Ty/r, Cx) Decoder inputs (previous decoder outputs)
    Returns:
        s: (N, Ty/r, Cx) Decoder outputs
        A: (N, Ty/r, Tx) attention
    """
    def __init__(self, enc_dim, dec_dim):
        super(AttentionRNN, self).__init__()
        self.gru = nn.GRU(dec_dim, dec_dim, num_layers=1, batch_first=True, bidirectional=False)
        self.att = BilinearAttention(enc_dim, dec_dim)

    def forward(self, h, s, prev_hidden=None):
        # Attention RNN
        s, hidden = self.gru(s, prev_hidden) # (N, Ty/r, Cx)
        A = self.att(h, s) # (N, Ty/r, Tx)
        return s, A, hidden


class MLPAttention(nn.Module):
    """
    BiLinear Attention
    h_T . W . s
    Args:
        h: (N, Tx, Cx) Encoder outputs
        s: (N, Ty/r, Cx) Decoder inputs (previous decoder outputs)
    Returns:
        A: (N, Ty/r, Tx) attention
    """
    def __init__(self, enc_dim, dec_dim):
        super(MLPAttention, self).__init__()
        self.W = nn.Linear(enc_dim+dec_dim, args.Ca, bias=True)
        self.v = nn.Linear(args.Ca, 1, bias=False)

    def forward(self, h, s):
        Tx, Ty = h.size(1), s.size(1)  # Tx, Ty
        hs = torch.cat([h.unsqueeze(1).expand(-1, Ty, -1, -1), s.unsqueeze(2).expand(-1, -1, Tx, -1)], dim=-1)
        e = self.v(torch.tanh(self.W(hs))).squeeze(-1)
        A = torch.softmax(e, dim=-1)
        return A


class BilinearAttention(nn.Module):
    """
    BiLinear Attention
    s_T . W . h
    Args:
        h: (N, Tx, Cx) Encoder outputs
        s: (N, Ty/r, Cx) Decoder inputs (previous decoder outputs)
    Returns:
        A: (N, Ty/r, Tx) attention
    """
    def __init__(self, enc_dim, dec_dim):
        super(BilinearAttention, self).__init__()
        self.W = nn.Linear(enc_dim, dec_dim)

    def forward(self, h, s):
        wh = self.W(h) # (N, Tx, Es)
        e = torch.matmul(wh, s.transpose(1, 2)) # (N, Tx, Ty)
        A = torch.softmax(e.transpose(1, 2), dim=-1) # (N, Ty, Tx)
        return A
