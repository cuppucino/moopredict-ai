import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
from loguru import logger
import yfinance as yf

# Deferred imports for torch and xgboost to avoid initialization conflicts
# They will be imported inside the methods that use them.

class LSTMModel: # Defined later to avoid torch dependency at import
    pass

class MLEngine:
    """
    Advanced ML Intelligence using LSTM (Price Prediction) and XGBoost (Trend Classification).
    """
    def __init__(self):
        self._device = None
        logger.info("[MLEngine] Initialized (Deferred Loading)")

    @property
    def device(self):
        if self._device is None:
            import torch
            self._device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        return self._device

    def predict_price_lstm(self, symbol: str, lookback_days: int = 365, pred_days: int = 5) -> Dict:
        """
        Train a quick LSTM on historical data and predict the next 'pred_days' prices.
        """
        try:
            import torch
            import torch.nn as nn
            
            # Local model definition
            class LocalLSTM(nn.Module):
                def __init__(self, input_size=1, hidden_layer_size=50, output_size=1):
                    super().__init__()
                    self.hidden_layer_size = hidden_layer_size
                    self.lstm = nn.LSTM(input_size, hidden_layer_size, batch_first=True)
                    self.linear = nn.Linear(hidden_layer_size, output_size)

                def forward(self, input_seq):
                    lstm_out, _ = self.lstm(input_seq)
                    predictions = self.linear(lstm_out[:, -1, :])
                    return predictions

            # 1. Fetch Data
            df = yf.download(symbol, period="2y", interval="1d", progress=False)
            if df.empty: return {"error": f"No data for {symbol}"}
            
            data = df['Close'].values.reshape(-1, 1)
            scaler = MinMaxScaler(feature_range=(-1, 1))
            scaled_data = scaler.fit_transform(data)
            
            # 2. Prepare Sequences
            seq_length = 30
            X, y = [], []
            for i in range(len(scaled_data) - seq_length):
                X.append(scaled_data[i:i+seq_length])
                y.append(scaled_data[i+seq_length])
            
            X = torch.FloatTensor(np.array(X)).to(self.device)
            y = torch.FloatTensor(np.array(y)).to(self.device)
            
            # 3. Train Model
            model = LocalLSTM().to(self.device)
            optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
            criterion = nn.MSELoss()
            
            model.train()
            for epoch in range(20):
                optimizer.zero_grad()
                y_pred = model(X)
                loss = criterion(y_pred, y)
                loss.backward()
                optimizer.step()
            
            # 4. Predict Future
            model.eval()
            last_seq = scaled_data[-seq_length:].reshape(1, seq_length, 1)
            last_seq = torch.FloatTensor(last_seq).to(self.device)
            
            predictions = []
            current_seq = last_seq
            
            for _ in range(pred_days):
                with torch.no_grad():
                    pred = model(current_seq)
                    predictions.append(pred.item())
                    new_val = pred.view(1, 1, 1)
                    current_seq = torch.cat((current_seq[:, 1:, :], new_val), dim=1)
            
            rescaled_preds = scaler.inverse_transform(np.array(predictions).reshape(-1, 1))
            current_price = float(df['Close'].iloc[-1])
            final_pred = float(rescaled_preds[-1])
            change_pct = ((final_pred - current_price) / current_price) * 100
            
            return {
                "symbol": symbol,
                "current_price": round(current_price, 2),
                "predicted_price_5d": round(final_pred, 2),
                "predicted_change_pct": round(change_pct, 2),
                "trend": "BULLISH" if change_pct > 0 else "BEARISH",
                "confidence": round(max(0, 100 - (loss.item() * 1000)), 2),
                "method": "LSTM (PyTorch)"
            }
        except Exception as e:
            logger.error(f"[MLEngine] LSTM error for {symbol}: {e}")
            return {"error": str(e)}

    def predict_trend_random_forest(self, symbol: str) -> Dict:
        """
        Use Random Forest to classify the next day's trend based on technical features.
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            
            df = yf.download(symbol, period="1y", interval="1d", progress=False)
            if len(df) < 50: return {"error": "Not enough data for ML"}
            
            # Feature Engineering
            df['RSI'] = self._calculate_rsi(df['Close'])
            df['SMA_20'] = df['Close'].rolling(window=20).mean()
            df['SMA_50'] = df['Close'].rolling(window=50).mean()
            df['Vol_SMA'] = df['Volume'].rolling(window=20).mean()
            df['Target'] = (df['Close'].shift(-1) > df['Close']).astype(int)
            
            df = df.dropna()
            features = ['RSI', 'SMA_20', 'SMA_50', 'Vol_SMA']
            X = df[features]
            y = df['Target']
            
            model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
            model.fit(X, y)
            
            last_features = X.iloc[-1:].values
            prob = model.predict_proba(last_features)[0][1]
            
            return {
                "symbol": symbol,
                "up_probability": round(float(prob) * 100, 2),
                "trend": "BULLISH" if prob > 0.5 else "BEARISH",
                "confidence": round(abs(prob - 0.5) * 200, 2),
                "method": "Random Forest Classifier (Scikit-Learn)"
            }
        except Exception as e:
            logger.error(f"[MLEngine] Random Forest error for {symbol}: {e}")
            return {"error": str(e)}

    def _calculate_rsi(self, series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

# Singleton
ml_engine = MLEngine()
