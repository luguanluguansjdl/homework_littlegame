using UnityEngine;

public class PlayerController : MonoBehaviour
{
    [Header("移动与跳跃设置")]
    public float moveSpeed = 5f;
    public float jumpForce = 7f;

    private bool isGrounded; // 是否在地面
    private bool jumpRequested; // 是否按下了跳跃键
    private bool hasDoubleJump; // 二段跳跃

    [Header("冲刺设置")]
    public KeyCode dashKey = KeyCode.LeftShift; // 自由调整按键
    public float dashSpeed = 15f;               // 冲刺速度
    public float dashDuration = 0.2f;           // 冲刺持续时间
    public float dashCooldown = 1.0f;           // 冷却时间

    private bool isDashing = false;
    private float dashTimeCounter;
    private float cooldownCounter;

    [Header("地面检测设置")]
    public Transform groundCheck;
    public float groundCheckRadius = 0.1f;
    public LayerMask groundLayer;

    private Rigidbody2D rb;
    

    void Start()
    {
        rb = GetComponent<Rigidbody2D>();
    }

    void Update()
    {
        if (Input.GetKeyDown(dashKey))// 捕获冲刺
        {
            StartDash();
        }
        if (groundCheck != null)// 地面检测
        {
            isGrounded = Physics2D.OverlapCircle(groundCheck.position, groundCheckRadius, groundLayer);
            if (isGrounded) { hasDoubleJump=true; }
        }
        if (Input.GetKeyDown(KeyCode.Space))// 捕获跳跃
        {
            jumpRequested = true;
        }

        if (isDashing)
        {
            dashTimeCounter -= Time.deltaTime;
            if (dashTimeCounter <= 0)
            {
                isDashing = false;
                rb.velocity = new Vector2(0, rb.velocity.y); // 冲刺结束，水平速度归零
                rb.gravityScale = 1f;
            }
        }
        // 冷却时间倒计时
        if (cooldownCounter > 0)
        {
            cooldownCounter -= Time.deltaTime;
        }
    }

    void FixedUpdate()
    {
        // 水平移动 (直接赋值速度，保持移动稳定)
        if (!isDashing)
        {
            float horizontalInput = Input.GetAxisRaw("Horizontal");
            rb.velocity = new Vector2(horizontalInput * moveSpeed, rb.velocity.y);
        }

        //  处理跳跃 (在固定物理帧中执行)
        if (jumpRequested){
            if (isGrounded)
            {
                rb.velocity = new Vector2(rb.velocity.x, jumpForce);// 赋予向上的瞬时速度
                jumpRequested = false; // 执行后立刻重置标记
                hasDoubleJump=true; // 可以二段跳
            }
            else if (hasDoubleJump)// 二段跳
            {
                rb.velocity = new Vector2(rb.velocity.x, jumpForce);
                jumpRequested = false;
                hasDoubleJump = false;
            }
            else
            {
                jumpRequested = false;// 直接作废这次按键请求
            }
        }
        
    }

    void StartDash()
    {
        // 如果正在冲刺或处于冷却中，直接返回
        if (isDashing || cooldownCounter > 0) return;

        isDashing = true;
        dashTimeCounter = dashDuration;
        cooldownCounter = dashCooldown;
        rb.gravityScale = 0.1f;

        // 决定冲刺方向：按着方向键就朝方向冲，没按就朝当前面朝的方向冲
        float direction = transform.localScale.x >= 0 ? 1 : -1;
        if (Input.GetAxisRaw("Horizontal") != 0)
        {
            direction = Input.GetAxisRaw("Horizontal");
        }

        // 赋予瞬间高速度
        rb.velocity = new Vector2(direction * dashSpeed, 0);
    }

    // 调试用：在 Scene 视图中画出检测圈
    void OnDrawGizmosSelected()
    {
        if (groundCheck != null)
        {
            Gizmos.color = Color.red;
            Gizmos.DrawWireSphere(groundCheck.position, groundCheckRadius);
        }
    }
}