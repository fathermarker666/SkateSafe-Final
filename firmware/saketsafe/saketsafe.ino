#include <Wire.h>

const int MPU_addr = 0x68; 
int16_t AcX, AcY, AcZ;

void setup() {
  Serial.begin(115200);
  Wire.begin(21, 22); // 強制指定腳位
  
  // 1. 喚醒 MPU6050
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x6B); // 電源管理暫存器
  Wire.write(0);    // 喚醒晶片
  Wire.endTransmission(true);

  // 2. 設定加速度計量程為 ±16G (關鍵修改)
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x1C); // 加速度配置暫存器 (ACCEL_CONFIG)
  Wire.write(0x18); // 寫入 0x18 代表設定為 ±16G
  Wire.endTransmission(true);
  
  Serial.println("✅ SkateSafe 16G 模式啟動！");
}

void loop() {
  Wire.beginTransmission(MPU_addr);
  Wire.write(0x3B); // 從 X 軸加速度開始讀
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_addr, 6, true);
  
  // 讀取原始數據
  AcX = Wire.read() << 8 | Wire.read();
  AcY = Wire.read() << 8 | Wire.read();
  AcZ = Wire.read() << 8 | Wire.read();

  // 3. 轉換比例修正：16G 模式下，比例因子為 2048.0 LSB/g
  float x_g = AcX / 2048.0;
  float y_g = AcY / 2048.0;
  float z_g = AcZ / 2048.0;
  
  // 計算總合向量 (Total G)
  float total_g = sqrt(x_g*x_g + y_g*y_g + z_g*z_g);

  // 輸出給網頁用的格式: Total_G, X_G, Y_G, Z_G
  Serial.print(total_g); Serial.print(",");
  Serial.print(x_g); Serial.print(",");
  Serial.print(y_g); Serial.print(",");
  Serial.println(z_g);

  delay(50); // Owen 測試過最穩定的間隔
}